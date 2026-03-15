"""Memory-bank scanner and Milady memory ingestor.

Scans ~/Desktop/*/memory-bank/ for project knowledge files,
chunks them into memory entries, and uploads to Milady + Wiki.js.

If Milady is offline, chunks are written to a pending folder
and processed on the next successful ingest cycle.
"""

import hashlib
import json
import logging
import re
import subprocess
import tempfile
import time
from pathlib import Path

import httpx

from .config import settings
from .models import IngestReport, IngestResult, ProjectInfo, ProjectKnowledge

logger = logging.getLogger(__name__)

# Files to read from memory-bank/, in priority order
MEMORY_BANK_FILES = [
    "activeContext.md",
    "progress.md",
    "techContext.md",
    "productContext.md",
    "systemPatterns.md",
    "projectbrief.md",
]


def discover_projects() -> list[ProjectInfo]:
    """Scan configured paths for project directories."""
    projects = []
    scan_paths = settings.get_scan_paths()

    if not scan_paths:
        logger.warning("No scan paths configured. Run setup or set DREAM_SCAN_PATHS.")
        return projects

    seen_names = set()
    for base_path in scan_paths:
        if not base_path.is_dir():
            continue
        for entry in sorted(base_path.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name.startswith("."):
                continue
            # Avoid duplicates if paths overlap
            if entry.name in seen_names:
                continue
            seen_names.add(entry.name)

            mb_path = entry / "memory-bank"
            claude_md = entry / "CLAUDE.md"
            status_md = entry / "STATUS.md"

            projects.append(ProjectInfo(
                name=entry.name,
                path=entry,
                memory_bank_path=mb_path if mb_path.is_dir() else None,
                claude_md_path=claude_md if claude_md.is_file() else None,
                status_md_path=status_md if status_md.is_file() else None,
                has_memory_bank=mb_path.is_dir(),
            ))

    return projects


def hash_directory(path: Path) -> str:
    """Compute a hash of all .md file mtimes in a directory."""
    h = hashlib.sha256()
    if not path.exists():
        return ""
    for f in sorted(path.glob("*.md")):
        h.update(f"{f.name}:{f.stat().st_mtime_ns}".encode())
    return h.hexdigest()[:16]


def load_state() -> dict:
    """Load ingest state (last hashes per project)."""
    state_file = settings.state_dir / "ingest_state.json"
    if state_file.exists():
        return json.loads(state_file.read_text())
    return {}


def save_state(state: dict):
    """Save ingest state."""
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    state_file = settings.state_dir / "ingest_state.json"
    state_file.write_text(json.dumps(state, indent=2))


def has_changed(project: ProjectInfo, state: dict) -> bool:
    """Check if a project's memory-bank changed since last ingest."""
    if not project.memory_bank_path:
        return False
    current_hash = hash_directory(project.memory_bank_path)
    stored = state.get(project.name, {})
    return current_hash != stored.get("hash", "")


def _extract_section(text: str, heading: str) -> str:
    """Extract content under a markdown heading."""
    pattern = rf"^##\s+{re.escape(heading)}.*?\n(.*?)(?=^##\s|\Z)"
    match = re.search(pattern, text, re.MULTILINE | re.DOTALL)
    if match:
        return match.group(1).strip()[:500]
    return ""


def _extract_first_paragraph(text: str) -> str:
    """Extract the first non-heading paragraph."""
    for line in text.split("\n"):
        line = line.strip()
        if line and not line.startswith("#") and not line.startswith("---"):
            return line[:400]
    return ""


def _extract_bullets(text: str, heading: str, max_items: int = 8) -> list[str]:
    """Extract bullet points under a heading."""
    section = _extract_section(text, heading)
    if not section:
        return []
    items = []
    for line in section.split("\n"):
        line = line.strip()
        if line.startswith("- ") or line.startswith("* "):
            items.append(line[2:].strip()[:200])
            if len(items) >= max_items:
                break
    return items


def read_project_knowledge(project: ProjectInfo) -> ProjectKnowledge | None:
    """Read all knowledge files for a project and build structured knowledge."""
    if not project.memory_bank_path:
        return None

    knowledge = ProjectKnowledge(
        name=project.name,
        slug=project.name.lower().replace(" ", "-"),
        path=project.path,
    )

    files_read = []

    # Read activeContext.md (most important)
    active_ctx_path = project.memory_bank_path / "activeContext.md"
    if active_ctx_path.is_file():
        text = active_ctx_path.read_text(errors="replace")
        knowledge.active_context_full = text[:15000]  # cap at 15KB
        knowledge.current_state = (
            _extract_section(text, "Current State")
            or _extract_section(text, "Current Status")
            or _extract_section(text, "What Is It")
            or _extract_first_paragraph(text)
        )
        files_read.append("activeContext.md")

    # Read projectbrief.md for summary
    brief_path = project.memory_bank_path / "projectbrief.md"
    if brief_path.is_file():
        text = brief_path.read_text(errors="replace")
        knowledge.summary = (
            _extract_section(text, "Purpose")
            or _extract_section(text, "Summary")
            or _extract_first_paragraph(text)
        )
        files_read.append("projectbrief.md")
    elif knowledge.current_state:
        # Fall back to first paragraph of activeContext
        knowledge.summary = knowledge.current_state[:300]

    # Read techContext.md for stack info
    tech_path = project.memory_bank_path / "techContext.md"
    if tech_path.is_file():
        text = tech_path.read_text(errors="replace")
        knowledge.tech_stack = (
            _extract_section(text, "Stack")
            or _extract_section(text, "Tech")
            or _extract_first_paragraph(text)
        )
        files_read.append("techContext.md")

    # Read progress.md for next steps and capabilities
    progress_path = project.memory_bank_path / "progress.md"
    if progress_path.is_file():
        text = progress_path.read_text(errors="replace")
        knowledge.next_steps = (
            _extract_section(text, "Next Steps")
            or _extract_section(text, "What's Left")
            or _extract_section(text, "TODO")
            or ""
        )[:400]
        knowledge.capabilities = (
            _extract_bullets(text, "What Works")
            or _extract_bullets(text, "Completed")
            or _extract_bullets(text, "Features")
        )
        files_read.append("progress.md")

    # Read CLAUDE.md from project root for quick-reference
    if project.claude_md_path and project.claude_md_path.is_file():
        text = project.claude_md_path.read_text(errors="replace")
        if not knowledge.tech_stack:
            knowledge.tech_stack = _extract_first_paragraph(text)[:300]
        files_read.append("CLAUDE.md")

    # Read any additional topic files
    if project.memory_bank_path:
        for md_file in sorted(project.memory_bank_path.glob("*.md")):
            if md_file.name not in [f for f in MEMORY_BANK_FILES] and md_file.name not in files_read:
                files_read.append(md_file.name)

    knowledge.files_read = files_read
    return knowledge


PENDING_DIR = settings.state_dir / "pending"


async def _milady_is_up(client: httpx.AsyncClient) -> bool:
    """Check if Milady is responding."""
    try:
        r = await client.get(f"{settings.milady_api_url}/api/memory/search?q=test&limit=1", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def _save_pending(knowledge: ProjectKnowledge):
    """Save chunks to pending folder for later upload when Milady is back."""
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    pending_file = PENDING_DIR / f"{knowledge.slug}.json"
    data = {
        "name": knowledge.name,
        "slug": knowledge.slug,
        "chunks": [{"text": c.text, "project": c.project, "type": c.chunk_type} for c in knowledge.to_memory_chunks()],
        "active_context": knowledge.active_context_full[:15000],
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    pending_file.write_text(json.dumps(data, indent=2))
    logger.info(f"Saved {len(data['chunks'])} chunks to pending for {knowledge.name}")


async def _process_pending(client: httpx.AsyncClient) -> int:
    """Upload any pending chunks from when Milady was offline."""
    if not PENDING_DIR.exists():
        return 0

    total = 0
    remember_url = f"{settings.milady_api_url}/api/memory/remember"
    doc_url = f"{settings.milady_api_url}/api/knowledge/documents"

    for pending_file in list(PENDING_DIR.glob("*.json")):
        try:
            data = json.loads(pending_file.read_text())
            uploaded = 0

            for chunk in data.get("chunks", []):
                try:
                    r = await client.post(remember_url, json={"text": chunk["text"]}, timeout=10)
                    if r.status_code < 300:
                        uploaded += 1
                except httpx.HTTPError:
                    pass

            # Upload knowledge doc too
            active_ctx = data.get("active_context", "")
            if active_ctx:
                try:
                    await client.post(doc_url, json={
                        "filename": f"dream-engine-{data['slug']}.md",
                        "content": active_ctx,
                    }, timeout=30)
                except httpx.HTTPError:
                    pass

            total += uploaded
            pending_file.unlink()
            logger.info(f"Processed pending: {data['name']} ({uploaded} chunks)")
        except Exception as e:
            logger.warning(f"Failed to process pending {pending_file.name}: {e}")

    return total


async def upload_to_milady(client: httpx.AsyncClient, knowledge: ProjectKnowledge) -> tuple[int, bool]:
    """Upload project knowledge chunks to Milady memory + knowledge doc."""
    chunks = knowledge.to_memory_chunks()
    chunks_uploaded = 0

    # Upload memory chunks
    remember_url = f"{settings.milady_api_url}/api/memory/remember"
    for chunk in chunks:
        try:
            resp = await client.post(
                remember_url,
                json={"text": chunk.text},
                timeout=10,
            )
            if resp.status_code < 300:
                chunks_uploaded += 1
            else:
                logger.warning(f"Memory upload failed for {knowledge.name}: {resp.status_code}")
        except httpx.HTTPError as e:
            logger.warning(f"Memory upload error for {knowledge.name}: {e}")

    # Upload full activeContext as knowledge document (vector search)
    doc_uploaded = False
    if knowledge.active_context_full:
        doc_url = f"{settings.milady_api_url}/api/knowledge/documents"
        try:
            resp = await client.post(
                doc_url,
                json={
                    "filename": f"dream-engine-{knowledge.slug}.md",
                    "content": knowledge.active_context_full,
                },
                timeout=30,
            )
            doc_uploaded = resp.status_code < 300
            if not doc_uploaded:
                logger.warning(f"Knowledge doc upload failed for {knowledge.name}: {resp.status_code} {resp.text[:200]}")
        except httpx.HTTPError as e:
            logger.warning(f"Knowledge doc upload error for {knowledge.name}: {e}")

    return chunks_uploaded, doc_uploaded


def update_wiki(knowledge: ProjectKnowledge) -> bool:
    """Update Wiki.js page for a project."""
    if not settings.wiki_enabled:
        return False
    script = Path(settings.wiki_update_script).expanduser()
    if not script.exists():
        logger.warning(f"Wiki update script not found: {script}")
        return False

    wiki_content = knowledge.to_wiki_page()
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(wiki_content)
            tmp_path = f.name

        result = subprocess.run(
            [str(script), f"apps/{knowledge.slug}", knowledge.name, tmp_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        Path(tmp_path).unlink(missing_ok=True)

        if result.returncode != 0:
            logger.warning(f"Wiki update failed for {knowledge.name}: {result.stderr[:200]}")
            return False
        return True
    except Exception as e:
        logger.warning(f"Wiki update error for {knowledge.name}: {e}")
        return False


async def ingest_all(force: bool = False) -> IngestReport:
    """Scan all projects, ingest changed memory-banks into Milady + Wiki."""
    start = time.time()
    projects = discover_projects()
    state = load_state()
    results = []
    total_chunks = 0
    ingested = 0
    skipped = 0

    async with httpx.AsyncClient() as client:
        milady_up = False
        if settings.milady_enabled:
            milady_up = await _milady_is_up(client)
            if not milady_up:
                logger.warning("Milady is offline — will save to pending folder")
            else:
                # Process any pending uploads from previous offline periods
                pending_count = await _process_pending(client)
                if pending_count > 0:
                    logger.info(f"Processed {pending_count} pending chunks from previous offline period")

        for project in projects:
            if not project.has_memory_bank:
                continue

            if not force and not has_changed(project, state):
                skipped += 1
                continue

            knowledge = read_project_knowledge(project)
            if not knowledge:
                skipped += 1
                continue

            logger.info(f"Ingesting {project.name} ({len(knowledge.files_read)} files)")

            chunks_uploaded = 0
            doc_uploaded = False
            if settings.milady_enabled:
                if milady_up:
                    chunks_uploaded, doc_uploaded = await upload_to_milady(client, knowledge)
                else:
                    _save_pending(knowledge)

            wiki_updated = False
            if settings.wiki_enabled:
                wiki_updated = update_wiki(knowledge)

            total_chunks += chunks_uploaded
            ingested += 1

            result = IngestResult(
                project=project.name,
                chunks_uploaded=chunks_uploaded,
                knowledge_doc_uploaded=doc_uploaded,
                wiki_updated=wiki_updated,
                timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
            )
            results.append(result)

            # Update state hash
            state[project.name] = {
                "hash": hash_directory(project.memory_bank_path),
                "last_ingested": result.timestamp,
                "chunks": chunks_uploaded,
            }

    save_state(state)
    duration = time.time() - start

    report = IngestReport(
        projects_discovered=len(projects),
        projects_ingested=ingested,
        projects_skipped=skipped,
        total_chunks=total_chunks,
        results=results,
        duration_sec=round(duration, 1),
    )

    logger.info(
        f"Ingest complete: {ingested} projects, {total_chunks} chunks, "
        f"{skipped} skipped, {duration:.1f}s"
    )
    return report


async def ingest_project(name: str) -> IngestResult | None:
    """Ingest a single project by name."""
    projects = discover_projects()
    project = next((p for p in projects if p.name == name), None)
    if not project or not project.has_memory_bank:
        return None

    knowledge = read_project_knowledge(project)
    if not knowledge:
        return None

    async with httpx.AsyncClient() as client:
        chunks_uploaded, doc_uploaded = await upload_to_milady(client, knowledge)

    wiki_updated = update_wiki(knowledge)

    state = load_state()
    state[name] = {
        "hash": hash_directory(project.memory_bank_path),
        "last_ingested": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "chunks": chunks_uploaded,
    }
    save_state(state)

    return IngestResult(
        project=name,
        chunks_uploaded=chunks_uploaded,
        knowledge_doc_uploaded=doc_uploaded,
        wiki_updated=wiki_updated,
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
    )
