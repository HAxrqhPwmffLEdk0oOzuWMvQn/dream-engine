"""Spark Dream Engine — FastAPI service for project knowledge ingestion and deep dreaming."""

import asyncio
import json
import logging
from datetime import datetime

from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse, StreamingResponse

from .config import settings
from .ingestor import discover_projects, ingest_all, ingest_project, load_state
from .models import DreamStatus, IngestReport

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("dream-engine")

from fastapi.staticfiles import StaticFiles

app = FastAPI(title="Spark Dream Engine", version="0.1.0")

# Dream site served at root — must be mounted AFTER all API routes (see bottom of file)

# Global dream status
dream_status = DreamStatus()


@app.get("/admin", response_class=HTMLResponse)
async def dashboard():
    """Simple dashboard showing project status and ingest controls."""
    projects = discover_projects()
    state = load_state()
    mb_projects = [p for p in projects if p.has_memory_bank]

    rows = []
    for p in mb_projects:
        s = state.get(p.name, {})
        last = s.get("last_ingested", "never")
        chunks = s.get("chunks", 0)
        rows.append(f"""
            <tr>
                <td class="px-4 py-2 font-mono text-sm">{p.name}</td>
                <td class="px-4 py-2 text-sm text-gray-400">{len(p.files_read) if hasattr(p, 'files_read') else '—'}</td>
                <td class="px-4 py-2 text-sm">{chunks} chunks</td>
                <td class="px-4 py-2 text-sm text-gray-400">{last}</td>
            </tr>
        """)

    return f"""<!DOCTYPE html>
<html>
<head>
    <title>Spark Dream Engine</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-950 text-gray-100 min-h-screen p-8">
    <div class="max-w-4xl mx-auto">
        <h1 class="text-3xl font-bold mb-2">Spark Dream Engine</h1>
        <p class="text-gray-400 mb-6">Knowledge ingestion + deep dreaming for Milady/Spark</p>

        <div class="flex gap-4 mb-8 flex-wrap">
            <button onclick="fetch('/api/ingest', {{method:'POST'}}).then(r=>r.json()).then(d=>{{alert('Ingested '+d.projects_ingested+' projects, '+d.total_chunks+' chunks');location.reload()}})"
                class="bg-blue-600 hover:bg-blue-700 px-4 py-2 rounded text-sm font-medium"
                title="Scan ~/Desktop/*/memory-bank/ for changes since last ingest. Upload new chunks to Milady memory + update Wiki.js pages. Fast — takes ~30 seconds.">
                Ingest Changed
            </button>
            <button onclick="fetch('/api/ingest?force=true', {{method:'POST'}}).then(r=>r.json()).then(d=>{{alert('Force ingested '+d.projects_ingested+' projects');location.reload()}})"
                class="bg-purple-600 hover:bg-purple-700 px-4 py-2 rounded text-sm font-medium"
                title="Re-ingest ALL projects regardless of whether they changed. Useful after Milady restart or to refresh stale data. Takes ~60 seconds.">
                Force Ingest All
            </button>
            <button onclick="if(confirm('Start full dream? This spawns Claude Code Opus for each of 5 project clusters and takes 1-2 hours.'))fetch('/api/dream/start', {{method:'POST'}}).then(r=>r.json()).then(d=>alert(JSON.stringify(d)))"
                class="bg-amber-600 hover:bg-amber-700 px-4 py-2 rounded text-sm font-medium"
                title="Deep analysis: spawns Claude Code Opus to read actual source code across 5 clusters (Photo, Business, AI Agents, Dashboards, Infra) + synthesis. Finds hidden capabilities, dead code, cross-project connections. Takes 1-2 hours.">
                Full Dream (Opus)
            </button>
            <button onclick="fetch('/api/dream/nightly', {{method:'POST'}}).then(r=>r.json()).then(d=>alert(JSON.stringify(d)))"
                class="bg-teal-600 hover:bg-teal-700 px-4 py-2 rounded text-sm font-medium"
                title="Quick dream: checks which projects changed since last dream, picks the top 3 stale ones, spawns Claude Code Opus for focused analysis on each. Takes 10-30 minutes.">
                Nightly Dream
            </button>
        </div>

        <div id="dream-status" class="bg-gray-900 rounded-lg p-4 mb-8 {'hidden' if not dream_status.running else ''}">
            <h2 class="text-lg font-semibold mb-2">Dream Status</h2>
            <p class="text-sm">Phase: <span class="text-amber-400">{dream_status.phase or 'idle'}</span></p>
            <p class="text-sm">Cluster: <span class="text-blue-400">{dream_status.current_cluster or '—'}</span></p>
            <p class="text-sm">Completed: {', '.join(dream_status.completed_clusters) or '—'}</p>
        </div>

        <div class="grid grid-cols-3 gap-4 mb-8">
            <div class="bg-gray-900 rounded-lg p-4">
                <div class="text-2xl font-bold">{len(projects)}</div>
                <div class="text-gray-400 text-sm">Total Projects</div>
            </div>
            <div class="bg-gray-900 rounded-lg p-4">
                <div class="text-2xl font-bold">{len(mb_projects)}</div>
                <div class="text-gray-400 text-sm">With Memory Banks</div>
            </div>
            <div class="bg-gray-900 rounded-lg p-4">
                <div class="text-2xl font-bold">{len(state)}</div>
                <div class="text-gray-400 text-sm">Ingested</div>
            </div>
        </div>

        <h2 class="text-xl font-semibold mb-3">Projects with Memory Banks</h2>
        <table class="w-full bg-gray-900 rounded-lg overflow-hidden">
            <thead>
                <tr class="bg-gray-800">
                    <th class="px-4 py-2 text-left text-sm">Project</th>
                    <th class="px-4 py-2 text-left text-sm">Files</th>
                    <th class="px-4 py-2 text-left text-sm">Ingested</th>
                    <th class="px-4 py-2 text-left text-sm">Last Ingest</th>
                </tr>
            </thead>
            <tbody class="divide-y divide-gray-800">
                {''.join(rows)}
            </tbody>
        </table>
    </div>
</body>
</html>"""


@app.post("/api/ingest")
async def api_ingest(force: bool = False) -> IngestReport:
    """Ingest all changed memory-banks into Milady + Wiki.js."""
    return await ingest_all(force=force)


@app.post("/api/ingest/{project_name}")
async def api_ingest_project(project_name: str):
    """Ingest a single project's memory-bank."""
    result = await ingest_project(project_name)
    if not result:
        raise HTTPException(404, f"Project '{project_name}' not found or has no memory-bank")
    return result


@app.get("/api/projects")
async def api_projects():
    """List all discovered projects with ingest status."""
    projects = discover_projects()
    state = load_state()
    return [
        {
            "name": p.name,
            "has_memory_bank": p.has_memory_bank,
            "has_claude_md": p.claude_md_path is not None,
            "has_status_md": p.status_md_path is not None,
            "last_ingested": state.get(p.name, {}).get("last_ingested"),
            "chunks": state.get(p.name, {}).get("chunks", 0),
        }
        for p in projects
    ]


@app.get("/api/projects/{project_name}")
async def api_project_detail(project_name: str):
    """Get detailed knowledge for a project."""
    from .ingestor import read_project_knowledge

    projects = discover_projects()
    project = next((p for p in projects if p.name == project_name), None)
    if not project:
        raise HTTPException(404, f"Project '{project_name}' not found")

    knowledge = read_project_knowledge(project)
    if not knowledge:
        raise HTTPException(404, f"No knowledge available for '{project_name}'")

    return {
        "name": knowledge.name,
        "summary": knowledge.summary,
        "current_state": knowledge.current_state,
        "tech_stack": knowledge.tech_stack,
        "capabilities": knowledge.capabilities,
        "next_steps": knowledge.next_steps,
        "files_read": knowledge.files_read,
    }


@app.post("/api/dream/start")
async def api_dream_start():
    """Trigger a full dream cycle (all clusters + synthesis). Runs in background."""
    if dream_status.running:
        raise HTTPException(409, "Dream already running")

    from .dreamer import dream_full

    async def run_dream():
        try:
            await dream_full(dream_status)
        except Exception as e:
            logger.error(f"Dream failed: {e}", exc_info=True)
            dream_status.running = False
            dream_status.phase = f"error: {e}"

    asyncio.create_task(run_dream())
    return {"status": "started", "mode": "full"}


@app.post("/api/dream/nightly")
async def api_dream_nightly():
    """Trigger a nightly dream (stale projects only). Runs in background."""
    if dream_status.running:
        raise HTTPException(409, "Dream already running")

    from .dreamer import dream_nightly

    async def run_dream():
        try:
            await dream_nightly(dream_status)
        except Exception as e:
            logger.error(f"Nightly dream failed: {e}", exc_info=True)
            dream_status.running = False
            dream_status.phase = f"error: {e}"

    asyncio.create_task(run_dream())
    return {"status": "started", "mode": "nightly"}


@app.get("/api/dream/status")
async def api_dream_status():
    """Get current dream status."""
    return dream_status


@app.get("/api/dream/history")
async def api_dream_history():
    """Get past dream runs."""
    history_path = settings.state_dir / "dream_history.json"
    if history_path.exists():
        return json.loads(history_path.read_text())
    return []


@app.post("/api/site/regenerate")
async def api_regenerate_site():
    """Regenerate the dream site from current data."""
    from .sitegen import regenerate_site
    regenerate_site()
    return {"status": "regenerated"}


@app.get("/api/dream/log")
async def api_dream_log(tail: int = 80):
    """Get the tail of the active dream output (analysis.md or dream.log)."""
    dreams_dir = settings.dreams_dir
    if not dreams_dir.exists():
        return PlainTextResponse("No dreams directory")

    # Find the most recently modified analysis.md (the actual output)
    analysis_files = sorted(
        dreams_dir.glob("**/analysis.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    # Also check dream.log
    log_files = sorted(
        dreams_dir.glob("**/dream.log"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    # Pick whichever is newer and non-empty
    best = None
    for candidates in [analysis_files, log_files]:
        for f in candidates:
            if f.stat().st_size > 0:
                if best is None or f.stat().st_mtime > best.stat().st_mtime:
                    best = f
                break

    if not best:
        if dream_status.running:
            return PlainTextResponse(f"Dream running ({dream_status.phase})... waiting for output")
        return PlainTextResponse("No dream output found")

    try:
        lines = best.read_text(errors="replace").splitlines()
        tail_lines = lines[-tail:] if len(lines) > tail else lines
        header = f"[{best.parent.name}/{best.name} — {best.stat().st_size} bytes]\n\n"
        return PlainTextResponse(header + "\n".join(tail_lines))
    except Exception as e:
        return PlainTextResponse(f"Error reading: {e}")


@app.get("/api/dream/log/stream")
async def api_dream_log_stream():
    """SSE stream of the active dream log (for live watching)."""
    dreams_dir = settings.dreams_dir
    log_files = sorted(dreams_dir.glob("**/dream.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not log_files:
        return PlainTextResponse("No active dream log")

    log_file = log_files[0]

    async def event_stream():
        last_pos = 0
        while True:
            try:
                if log_file.exists():
                    content = log_file.read_text(errors="replace")
                    if len(content) > last_pos:
                        new_content = content[last_pos:]
                        last_pos = len(content)
                        for line in new_content.splitlines():
                            if line.strip():
                                yield f"data: {line}\n\n"
            except Exception:
                pass
            await asyncio.sleep(2)

            # Stop streaming if dream is done
            if not dream_status.running and last_pos > 0:
                yield "data: [dream complete]\n\n"
                break

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# Mount dream site at root — MUST be last so API routes take priority
_site_dir = Path(__file__).parent.parent / "site"
if _site_dir.exists():
    app.mount("/", StaticFiles(directory=str(_site_dir), html=True), name="dream-site")
