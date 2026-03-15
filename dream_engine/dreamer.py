"""Dream orchestration — full and nightly modes.

Full mode: Analyze all projects in clusters, then synthesize.
Nightly mode: Re-analyze stale projects only.
"""

import asyncio
import glob
import json
import logging
import shutil
import time
from datetime import datetime
from pathlib import Path

import httpx

from .config import settings
from .ingestor import discover_projects, has_changed, hash_directory, load_state, save_state
from .models import DreamStatus
from .parser import parse_dream_markdown, parse_workspace_files
from .spawner import spawn_claude

SITE_DIR = Path(__file__).parent.parent / "site"


def _update_site(dream_dir: Path):
    """Copy any HTML/CSS from the latest dream workspace to the site folder."""
    SITE_DIR.mkdir(parents=True, exist_ok=True)

    # Look for HTML in synthesis dir first, then any workspace
    candidates = [
        dream_dir / "synthesis",
        dream_dir,
    ]
    # Also check all subdirectories
    for subdir in sorted(dream_dir.iterdir()):
        if subdir.is_dir() and subdir not in candidates:
            candidates.append(subdir)

    for candidate in candidates:
        html_files = list(candidate.glob("*.html"))
        if html_files:
            for f in html_files:
                shutil.copy2(f, SITE_DIR / f.name)
                logger.info(f"Updated site: {f.name}")
            # Also copy CSS if present
            for f in candidate.glob("*.css"):
                shutil.copy2(f, SITE_DIR / f.name)
            logger.info(f"Site updated from {candidate}")
            return

    logger.info("No HTML output to update site with")

logger = logging.getLogger(__name__)

CLUSTER_SIZE = 10  # max projects per cluster


def _auto_cluster(project_names: list[str]) -> dict[str, list[str]]:
    """Auto-cluster projects into batches.

    If a clusters.json exists in the config dir, use that.
    Otherwise, split alphabetically into batches of CLUSTER_SIZE.
    """
    clusters_file = settings.state_dir / "clusters.json"
    if clusters_file.exists():
        try:
            return json.loads(clusters_file.read_text())
        except (json.JSONDecodeError, Exception):
            pass

    # Default: alphabetical batches
    sorted_names = sorted(project_names)
    clusters = {}
    for i in range(0, len(sorted_names), CLUSTER_SIZE):
        batch = sorted_names[i:i + CLUSTER_SIZE]
        cluster_name = f"batch-{i // CLUSTER_SIZE + 1}"
        clusters[cluster_name] = batch
    return clusters


def _build_cluster_prompt(cluster_name: str, project_names: list[str]) -> str:
    """Build a dream prompt for a cluster of projects."""
    all_projects = discover_projects()
    name_to_path = {p.name: str(p.path) for p in all_projects}
    project_paths = " ".join(f"{name_to_path.get(p, p)}/" for p in project_names)
    return (
        f"Dream deep dive: {cluster_name} cluster. "
        f"Read EVERY source file (.py .ts .js .go) in these projects: {project_paths}. "
        f"For each project, read all source files — not just README. "
        f"Quote interesting code with file:line references. "
        f"Write analysis.md with per-project deep analysis: "
        f"what it actually does (from source code), hidden capabilities, "
        f"dead code worth reviving, architectural strengths and weaknesses, "
        f"integration points with other projects in this cluster and beyond. "
        f"Include the JSON output block as specified in CLAUDE.md."
    )


def _build_focused_prompt(project_name: str) -> str:
    """Build a dream prompt for a single project."""
    all_projects = discover_projects()
    project_path = next((str(p.path) for p in all_projects if p.name == project_name), project_name)
    return (
        f"Dream focused dive: {project_path}/. "
        f"Read EVERY source file (.py .ts .js .go .mjs) in this project. "
        f"Understand the full architecture from source code. "
        f"Write analysis.md with: what it does, hidden capabilities, "
        f"dead code, architectural insights, specific file:line quotes. "
        f"Also check if it has a memory-bank/ and read those files for context. "
        f"Include the JSON output block as specified in CLAUDE.md."
    )


def _build_synthesis_prompt(cluster_dirs: list[str]) -> str:
    """Build the synthesis prompt that reads all cluster outputs."""
    reads = " ".join(f"Read all .md files in {d}." for d in cluster_dirs)
    return (
        f"Dream grand synthesis. {reads} "
        f"Also read /tmp/spark-kb-export.md for wiki context. "
        f"Cross-reference ALL cluster analyses. Identify: "
        f"1) Cross-project connections with specific file paths and API endpoints. "
        f"2) The top 10 unrealized potentials across all projects. "
        f"3) 5 new project ideas that combine existing infrastructure. "
        f"4) The most surprising/forgotten code discoveries. "
        f"Write analysis.md with comprehensive synthesis. "
        f"Include the JSON output block as specified in CLAUDE.md."
    )


async def _ingest_dream_output(result: dict, label: str):
    """Ingest dream findings into Milady memory."""
    structured = result.get("json_output")
    if not structured:
        # Fall back to parsing markdown
        analysis = result.get("analysis_md", "")
        if analysis:
            try:
                structured = parse_dream_markdown(analysis)
            except Exception as e:
                logger.warning(f"Failed to parse dream markdown for {label}: {e}")
                structured = None

    if not structured or not structured.get("projects"):
        logger.warning(f"No structured output to ingest for {label}")
        return

    async with httpx.AsyncClient() as client:
        remember_url = f"{settings.milady_api_url}/api/memory/remember"
        chunks_uploaded = 0

        # Ingest project insights
        for proj in structured.get("projects", []):
            name = proj.get("name", "")
            if not name:
                continue

            # Hidden capabilities
            for cap in proj.get("hidden_capabilities", []):
                text = f"[dream:{label}] [project:{name}] [hidden] {cap}"
                try:
                    await client.post(remember_url, json={"text": text}, timeout=10)
                    chunks_uploaded += 1
                except httpx.HTTPError:
                    pass

            # Insights
            for insight in proj.get("insights", []):
                text = f"[dream:{label}] [project:{name}] [insight] {insight}"
                try:
                    await client.post(remember_url, json={"text": text}, timeout=10)
                    chunks_uploaded += 1
                except httpx.HTTPError:
                    pass

        # Ingest cross-project connections
        for conn in structured.get("cross_project_connections", []):
            source = conn.get("source", "")
            target = conn.get("target", "")
            desc = conn.get("description", "")
            if source and target and desc:
                text = f"[dream:{label}] [connection:{source}<>{target}] {desc}"
                try:
                    await client.post(remember_url, json={"text": text}, timeout=10)
                    chunks_uploaded += 1
                except httpx.HTTPError:
                    pass

        # Ingest dream proposals
        for prop in structured.get("dream_proposals", []):
            name = prop.get("name", "")
            desc = prop.get("description", "")
            if name and desc:
                text = f"[dream:{label}] [proposal] {name}: {desc[:300]}"
                try:
                    await client.post(remember_url, json={"text": text}, timeout=10)
                    chunks_uploaded += 1
                except httpx.HTTPError:
                    pass

        logger.info(f"Ingested {chunks_uploaded} dream chunks for {label}")


async def dream_full(status: DreamStatus) -> dict:
    """Run a full dream cycle — all clusters + synthesis.

    Returns summary dict with results per phase.
    """
    dream_id = datetime.now().strftime("%Y-%m-%d-%H%M")
    dream_dir = settings.dreams_dir / dream_id
    dream_dir.mkdir(parents=True, exist_ok=True)

    status.running = True
    status.phase = "inventory"
    status.started_at = datetime.now().isoformat()
    status.completed_clusters = []

    results = {}
    cluster_dirs = []

    # Refresh wiki export if enabled
    if settings.wiki_enabled and settings.wiki_export_script:
        try:
            import subprocess
            script = Path(settings.wiki_export_script).expanduser()
            if script.exists():
                subprocess.run(
                    [str(script), "/tmp/dream-engine-kb-export.md"],
                    capture_output=True, timeout=30,
                )
        except Exception as e:
            logger.warning(f"Wiki export failed: {e}")

    # Auto-cluster projects
    all_projects = discover_projects()
    project_names = [p.name for p in all_projects if p.has_memory_bank]
    clusters = _auto_cluster(project_names)

    # Phase: Cluster deep dives
    for cluster_name, project_names in clusters.items():
        status.phase = f"cluster-{cluster_name}"
        status.current_cluster = cluster_name

        cluster_dir = dream_dir / f"cluster-{cluster_name}"
        prompt = _build_cluster_prompt(cluster_name, project_names)

        logger.info(f"Starting cluster: {cluster_name} ({len(project_names)} projects)")

        result = await spawn_claude(
            task_prompt=prompt,
            workspace_dir=cluster_dir,
            timeout_sec=settings.cluster_timeout_sec,
        )

        results[cluster_name] = {
            "duration_sec": result["duration_sec"],
            "timed_out": result["timed_out"],
            "exit_code": result["exit_code"],
            "has_json": result["json_output"] is not None,
            "analysis_size": len(result.get("analysis_md", "")),
        }

        # Ingest findings into Milady
        await _ingest_dream_output(result, f"{dream_id}-{cluster_name}")

        cluster_dirs.append(str(cluster_dir))
        status.completed_clusters.append(cluster_name)
        logger.info(f"Cluster {cluster_name} done: {result['duration_sec']}s")

    # Phase: Synthesis
    status.phase = "synthesis"
    status.current_cluster = ""

    synthesis_dir = dream_dir / "synthesis"
    synthesis_prompt = _build_synthesis_prompt(cluster_dirs)

    logger.info("Starting grand synthesis")
    result = await spawn_claude(
        task_prompt=synthesis_prompt,
        workspace_dir=synthesis_dir,
        timeout_sec=settings.synthesis_timeout_sec,
    )

    results["synthesis"] = {
        "duration_sec": result["duration_sec"],
        "timed_out": result["timed_out"],
        "exit_code": result["exit_code"],
        "has_json": result["json_output"] is not None,
        "analysis_size": len(result.get("analysis_md", "")),
    }
    await _ingest_dream_output(result, f"{dream_id}-synthesis")

    # Save dream history
    total_duration = time.time() - (
        datetime.fromisoformat(status.started_at).timestamp()
        if status.started_at else time.time()
    )
    history_entry = {
        "dream_id": dream_id,
        "mode": "full",
        "started_at": status.started_at,
        "duration_sec": round(total_duration, 1),
        "clusters": results,
        "dream_dir": str(dream_dir),
    }
    _append_dream_history(history_entry)

    status.running = False
    status.phase = "complete"
    status.elapsed_sec = round(total_duration, 1)

    # Regenerate the dream site from all accumulated data
    try:
        from .sitegen import regenerate_site
        regenerate_site()
    except Exception as e:
        logger.warning(f"Site regeneration failed: {e}")

    # Post-dream: re-ingest memory-banks (updates site + optional integrations)
    logger.info("Post-dream: re-ingesting memory-banks")
    try:
        from .ingestor import ingest_all
        await ingest_all(force=False)
    except Exception as e:
        logger.warning(f"Post-dream ingest failed: {e}")

    logger.info(f"Full dream complete: {dream_id}, {total_duration:.0f}s")
    return history_entry


async def dream_nightly(status: DreamStatus) -> dict:
    """Run nightly dream — only stale/changed projects.

    Checks which projects changed since last dream, picks top 3, runs focused dreams.
    """
    dream_id = datetime.now().strftime("%Y-%m-%d-nightly")
    dream_dir = settings.dreams_dir / dream_id
    dream_dir.mkdir(parents=True, exist_ok=True)

    status.running = True
    status.phase = "staleness-check"
    status.started_at = datetime.now().isoformat()

    state = load_state()
    projects = discover_projects()

    # Find stale projects (changed since last ingest, or never dreamed)
    stale = []
    for project in projects:
        if not project.has_memory_bank:
            continue
        if has_changed(project, state):
            stale.append(project)

    # Also include projects never deeply dreamed (check dream history)
    dream_history = _load_dream_history()
    dreamed_projects = set()
    for entry in dream_history:
        for cluster_name, cluster_data in entry.get("clusters", {}).items():
            if cluster_name in CLUSTERS:
                dreamed_projects.update(CLUSTERS[cluster_name])

    for project in projects:
        if project.name not in dreamed_projects and project.has_memory_bank:
            if project not in stale:
                stale.append(project)

    # Pick top 3 most stale
    targets = stale[:3]

    if not targets:
        logger.info("No stale projects — skipping nightly dream")
        status.running = False
        status.phase = "skipped"
        return {"dream_id": dream_id, "mode": "nightly", "skipped": True, "reason": "no stale projects"}

    results = {}
    for project in targets:
        status.phase = f"focused-{project.name}"
        status.current_cluster = project.name

        project_dir = dream_dir / project.name
        prompt = _build_focused_prompt(project.name)

        logger.info(f"Nightly dream: {project.name}")

        result = await spawn_claude(
            task_prompt=prompt,
            workspace_dir=project_dir,
            max_turns=500,  # Less turns for focused dreams
            timeout_sec=1800,  # 30 min max
        )

        results[project.name] = {
            "duration_sec": result["duration_sec"],
            "timed_out": result["timed_out"],
            "has_json": result["json_output"] is not None,
            "analysis_size": len(result.get("analysis_md", "")),
        }

        await _ingest_dream_output(result, f"{dream_id}-{project.name}")
        status.completed_clusters.append(project.name)

    total_duration = time.time() - datetime.fromisoformat(status.started_at).timestamp()
    history_entry = {
        "dream_id": dream_id,
        "mode": "nightly",
        "started_at": status.started_at,
        "duration_sec": round(total_duration, 1),
        "targets": [p.name for p in targets],
        "results": results,
        "dream_dir": str(dream_dir),
    }
    _append_dream_history(history_entry)

    status.running = False
    status.phase = "complete"
    status.elapsed_sec = round(total_duration, 1)

    # Regenerate the dream site from all accumulated data
    try:
        from .sitegen import regenerate_site
        regenerate_site()
    except Exception as e:
        logger.warning(f"Site regeneration failed: {e}")

    # Post-dream: re-ingest memory-banks to catch any updates
    logger.info("Post-dream: re-ingesting memory-banks")
    try:
        from .ingestor import ingest_all
        await ingest_all(force=False)
    except Exception as e:
        logger.warning(f"Post-dream ingest failed: {e}")

    logger.info(f"Nightly dream complete: {dream_id}, {len(targets)} projects, {total_duration:.0f}s")
    return history_entry


def _load_dream_history() -> list:
    """Load dream history."""
    path = settings.state_dir / "dream_history.json"
    if path.exists():
        return json.loads(path.read_text())
    return []


def _append_dream_history(entry: dict):
    """Append to dream history."""
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    history = _load_dream_history()
    history.append(entry)
    # Keep last 50 entries
    history = history[-50:]
    path = settings.state_dir / "dream_history.json"
    path.write_text(json.dumps(history, indent=2))
