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
    """Admin dashboard — ingests, dreams, site regen."""
    projects = discover_projects()
    state = load_state()
    mb_projects = [p for p in projects if p.has_memory_bank]

    # Dream history count
    dream_count = 0
    history_path = settings.state_dir / "dream_history.json"
    if history_path.exists():
        try:
            dream_count = len(json.loads(history_path.read_text()))
        except Exception:
            pass

    # Project table rows
    rows = ""
    for p in mb_projects:
        s = state.get(p.name, {})
        last = s.get("last_ingested", "—")
        chunks = s.get("chunks", 0)
        rows += f"<tr><td>{p.name}</td><td>{chunks}</td><td>{last}</td></tr>\n"

    # Live dream banner (shown when a dream is running)
    if dream_status.running:
        cluster_bit = f'<span style="color:var(--text-muted);font-size:0.85rem;margin-left:1.5rem">cluster: <strong style="color:var(--text-secondary)">{dream_status.current_cluster}</strong></span>' if dream_status.current_cluster else ""
        dream_banner = f"""<div style="background:linear-gradient(90deg,#141414,rgba(124,92,191,0.1));border-bottom:1px solid var(--accent-dim);padding:0.75rem 0">
  <div class="container" style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:0.75rem;padding-top:0;padding-bottom:0">
    <div style="display:flex;align-items:center;gap:0.75rem">
      <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--status-active);box-shadow:0 0 8px var(--status-active);animation:pulse-glow 1.5s ease-in-out infinite"></span>
      <strong style="color:var(--accent-bright)">Dream Running</strong>
      <span style="color:var(--text-muted);font-size:0.85rem">phase: <strong style="color:var(--text-secondary)">{dream_status.phase or 'initializing'}</strong></span>
      {cluster_bit}
    </div>
    <a href="/api/dream/log" target="_blank" style="font-size:0.8rem;color:var(--accent-bright);border:1px solid var(--accent-dim);padding:0.3rem 0.75rem;border-radius:4px;text-decoration:none">View Log ↗</a>
  </div>
</div>"""
        auto_refresh = "setTimeout(() => location.reload(), 10000);"
    else:
        dream_banner = ""
        auto_refresh = ""

    batch_size = settings.nightly_batch_size

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Admin — Dream Engine</title>
  <link rel="stylesheet" href="/style.css">
  <style>
    .action-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
      gap: 1.5rem;
      margin: 1.5rem 0 2.5rem;
    }}
    .action-card {{
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 1.5rem;
      display: flex;
      flex-direction: column;
    }}
    .action-card h3 {{
      color: var(--text-primary);
      font-size: 1rem;
      margin-bottom: 0.5rem;
    }}
    .action-desc {{
      font-size: 0.83rem;
      color: var(--text-muted);
      flex: 1;
      margin-bottom: 1.25rem;
      line-height: 1.6;
    }}
    .btn {{
      display: block;
      width: 100%;
      padding: 0.6rem 1.25rem;
      border-radius: 6px;
      border: none;
      font-size: 0.85rem;
      font-family: var(--font-mono);
      font-weight: 500;
      cursor: pointer;
      transition: all 0.15s;
      text-align: center;
      text-decoration: none;
    }}
    .btn-primary {{ background: var(--accent); color: white; }}
    .btn-primary:hover {{ background: var(--accent-bright); color: white; }}
    .btn-outline {{
      background: transparent;
      color: var(--accent-bright);
      border: 1px solid var(--accent-dim);
    }}
    .btn-outline:hover {{ background: rgba(124,92,191,0.15); }}
    .btn-muted {{
      background: var(--bg-tertiary);
      color: var(--text-secondary);
      border: 1px solid var(--border);
    }}
    .btn-muted:hover {{ background: var(--bg-hover); border-color: var(--border-accent); color: var(--text-primary); }}
    .btn:disabled {{ opacity: 0.4; cursor: not-allowed; }}
    .action-status {{
      font-size: 0.75rem;
      color: var(--text-muted);
      margin-top: 0.75rem;
      min-height: 1.1rem;
      font-family: var(--font-mono);
    }}
    .action-status.ok {{ color: var(--status-active); }}
    .action-status.err {{ color: var(--status-blocked); }}
    .section-label {{
      font-size: 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0.1em;
      color: var(--text-muted);
      padding-bottom: 0.75rem;
      border-bottom: 1px solid var(--border);
      margin-bottom: 1.5rem;
      margin-top: 2rem;
    }}
    .section-label::before {{
      content: "// ";
      color: var(--accent);
      font-family: var(--font-mono);
    }}
    .page-header {{ margin-bottom: 1.5rem; }}
    .page-header h1 {{ margin-bottom: 0.25rem; }}
    .page-header p {{ margin: 0; }}
  </style>
</head>
<body>
  <nav>
    <div class="container">
      <a href="/" class="nav-logo"><span class="spark">DREAM</span> ENGINE</a>
      <div class="nav-links">
        <a href="/">Home</a>
        <a href="/projects.html">Projects</a>
        <a href="/connections.html">Connections</a>
        <a href="/dreams.html">Dreams</a>
        <a href="/gaps.html">Gaps</a>
        <a href="/admin" class="active">Admin</a>
      </div>
    </div>
  </nav>

  {dream_banner}

  <div class="container">
    <div class="page-header">
      <h1>Admin</h1>
      <p style="color:var(--text-muted)">Trigger ingests, start dreams, regenerate the site.</p>
    </div>

    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-value">{len(projects)}</div>
        <div class="stat-label">Total Projects</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">{len(mb_projects)}</div>
        <div class="stat-label">With Memory Banks</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">{len(state)}</div>
        <div class="stat-label">Ingested</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">{dream_count}</div>
        <div class="stat-label">Dreams Run</div>
      </div>
    </div>

    <div class="section-label">Actions</div>
    <div class="action-grid">

      <div class="action-card">
        <h3>Ingest Changed</h3>
        <div class="action-desc">Scan project dirs for files that changed since last ingest. Uploads new chunks to Milady memory and updates Wiki.js pages. Fast — typically 10–30 seconds.</div>
        <button class="btn btn-primary" onclick="runAction('/api/ingest','POST','st-ingest','Scanning...', r=>'✓ '+r.projects_ingested+' projects, '+r.total_chunks+' chunks')">
          Run Ingest
        </button>
        <div id="st-ingest" class="action-status"></div>
      </div>

      <div class="action-card">
        <h3>Force Ingest All</h3>
        <div class="action-desc">Re-upload all projects regardless of whether they changed. Use this after restarting Milady, or to refresh stale knowledge. Takes ~60 seconds for large project sets.</div>
        <button class="btn btn-muted" onclick="runAction('/api/ingest?force=true','POST','st-force','Force ingesting all...', r=>'✓ '+r.projects_ingested+' projects re-uploaded')">
          Force Ingest All
        </button>
        <div id="st-force" class="action-status"></div>
      </div>

      <div class="action-card">
        <h3>Nightly Dream</h3>
        <div class="action-desc">Picks the {batch_size} most stale projects and spawns Claude Code Opus for focused source-code analysis on each. Runs in the background — typically 10–30 minutes.</div>
        <button class="btn btn-primary" id="btn-nightly" onclick="startDream('/api/dream/nightly','st-nightly',this)">
          Start Nightly Dream
        </button>
        <div id="st-nightly" class="action-status"></div>
      </div>

      <div class="action-card">
        <h3>Full Dream</h3>
        <div class="action-desc">Deep analysis across all project clusters plus a synthesis pass. Claude Code Opus reads actual source files — not just docs — and finds hidden connections. Takes 1–2 hours.</div>
        <button class="btn btn-outline" id="btn-full" onclick="confirmFull(this)">
          Start Full Dream
        </button>
        <div id="st-full" class="action-status"></div>
      </div>

      <div class="action-card">
        <h3>Regenerate Site</h3>
        <div class="action-desc">Rebuild the static dream site from current dream outputs and ingest state. Run this if the site looks stale after a dream completes or after manual edits to dream files.</div>
        <button class="btn btn-muted" onclick="runAction('/api/site/regenerate','POST','st-regen','Regenerating...', r=>'✓ Site regenerated')">
          Regenerate Site
        </button>
        <div id="st-regen" class="action-status"></div>
      </div>

    </div>

    <div class="section-label">Projects with Memory Banks</div>
    <table class="data-table" style="margin-bottom:2rem">
      <thead>
        <tr>
          <th>Project</th>
          <th>Chunks</th>
          <th>Last Ingested</th>
        </tr>
      </thead>
      <tbody>
        {rows}
      </tbody>
    </table>

    <div style="margin-bottom:3rem">
      <a href="/api/dream/log" target="_blank" class="btn btn-muted" style="display:inline-block;width:auto;padding:0.5rem 1.25rem">
        View Dream Log ↗
      </a>
    </div>
  </div>

  <footer>
    <div class="container"><p>Dream Engine — AI-powered project analysis</p></div>
  </footer>

  <script>
    async function runAction(url, method, sid, pending, fmt) {{
      const el = document.getElementById(sid);
      el.className = 'action-status';
      el.textContent = pending;
      try {{
        const r = await fetch(url, {{method}});
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail || r.statusText);
        el.className = 'action-status ok';
        el.textContent = fmt(d);
      }} catch(e) {{
        el.className = 'action-status err';
        el.textContent = '✗ ' + e.message;
      }}
    }}

    async function startDream(url, sid, btn) {{
      if (btn.disabled) return;
      const el = document.getElementById(sid);
      el.className = 'action-status';
      el.textContent = 'Starting...';
      btn.disabled = true;
      try {{
        const r = await fetch(url, {{method:'POST'}});
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail || r.statusText);
        el.className = 'action-status ok';
        el.textContent = '✓ Dream started — running in background';
        setTimeout(() => location.reload(), 2000);
      }} catch(e) {{
        el.className = 'action-status err';
        el.textContent = '✗ ' + e.message;
        btn.disabled = false;
      }}
    }}

    function confirmFull(btn) {{
      if (!confirm('Start a full dream?\\n\\nClaude Code Opus will analyze all project clusters + synthesis. This takes 1–2 hours.')) return;
      startDream('/api/dream/start', 'st-full', btn);
    }}

    {auto_refresh}
  </script>
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


@app.post("/api/proposals/star/{proposal_id}")
async def api_proposal_star(proposal_id: str, starred: bool = True):
    """Star or unstar a dream proposal."""
    path = settings.state_dir / "starred_proposals.json"
    data = json.loads(path.read_text()) if path.exists() else {"starred": {}}
    if starred:
        data["starred"][proposal_id] = {"timestamp": datetime.now().isoformat()}
    else:
        data["starred"].pop(proposal_id, None)
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))
    return {"ok": True, "proposal_id": proposal_id, "starred": starred}


@app.get("/api/proposals/starred")
async def api_proposals_starred():
    """Get starred proposals."""
    path = settings.state_dir / "starred_proposals.json"
    if path.exists():
        return json.loads(path.read_text())
    return {"starred": {}}


@app.get("/api/gaps/actions")
async def api_gaps_actions():
    """Get current gaps action state (done/dismissed items)."""
    path = settings.state_dir / "gaps_actions.json"
    if path.exists():
        return json.loads(path.read_text())
    return {"actions": {}}


@app.post("/api/gaps/actions/{item_id}")
async def api_gaps_action(item_id: str, status: str = "done"):
    """Mark a gaps item as done, dismissed, or open (re-open)."""
    if status not in ("done", "dismissed", "open"):
        raise HTTPException(400, "Status must be done, dismissed, or open")
    path = settings.state_dir / "gaps_actions.json"
    data = json.loads(path.read_text()) if path.exists() else {"actions": {}}
    if status == "open":
        data["actions"].pop(item_id, None)
    else:
        data["actions"][item_id] = {
            "status": status,
            "timestamp": datetime.now().isoformat(),
        }
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))
    return {"ok": True, "item_id": item_id, "status": status}


# Mount dream site at root — MUST be last so API routes take priority
_site_dir = Path(__file__).parent.parent / "site"
if _site_dir.exists():
    app.mount("/", StaticFiles(directory=str(_site_dir), html=True), name="dream-site")
