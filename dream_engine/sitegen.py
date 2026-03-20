"""Site generator — reads dream outputs + ingested knowledge, regenerates HTML pages.

Keeps the existing style.css and nav structure. Replaces page content with
current data from dream analyses, ingest state, and Milady memory.
"""

import html
import json
import logging
import re
import shutil
from datetime import datetime
from pathlib import Path

from .config import settings
from .parser import extract_narrative

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent.parent / "site-template"

SITE_DIR = Path(__file__).parent.parent / "site"
DREAMS_DIR = settings.dreams_dir
STATE_DIR = settings.state_dir


def _esc(s: str) -> str:
    """HTML-escape a string."""
    return html.escape(str(s)) if s else ""


def _load_ingest_state() -> dict:
    path = STATE_DIR / "ingest_state.json"
    if path.exists():
        return json.loads(path.read_text())
    return {}


def _load_dream_history() -> list:
    path = STATE_DIR / "dream_history.json"
    if path.exists():
        return json.loads(path.read_text())
    return []


def _load_all_analyses() -> dict[str, str]:
    """Load all analysis.md files from dream directories."""
    analyses = {}
    if not DREAMS_DIR.exists():
        return analyses
    for md_file in DREAMS_DIR.glob("**/analysis.md"):
        project_name = md_file.parent.name
        analyses[project_name] = md_file.read_text(errors="replace")
    return analyses


def _load_dream_run_analyses() -> dict[str, dict[str, str]]:
    """Load analyses grouped by dream run (date directory).

    Returns {dream_id: {subdir_name: analysis_text}}.
    """
    runs = {}
    if not DREAMS_DIR.exists():
        return runs
    for date_dir in sorted(DREAMS_DIR.iterdir()):
        if not date_dir.is_dir():
            continue
        dream_id = date_dir.name
        run = {}
        for sub in sorted(date_dir.iterdir()):
            if not sub.is_dir():
                continue
            analysis = sub / "analysis.md"
            if analysis.is_file() and analysis.stat().st_size > 0:
                run[sub.name] = analysis.read_text(errors="replace")
        if run:
            runs[dream_id] = run
    return runs


def _build_analysis_date_map() -> dict[str, str]:
    """Map analysis subdir names to their dream run date.

    Returns {subdir_name: dream_id} for the most recent analysis of each name.
    """
    date_map = {}
    if not DREAMS_DIR.exists():
        return date_map
    for date_dir in sorted(DREAMS_DIR.iterdir()):
        if not date_dir.is_dir():
            continue
        dream_id = date_dir.name
        for sub in date_dir.iterdir():
            if sub.is_dir() and (sub / "analysis.md").is_file():
                date_map[sub.name] = dream_id
    return date_map


def _format_date(iso_str: str) -> str:
    """Format an ISO date string to a short human-readable form."""
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%b %d, %Y")
    except (ValueError, TypeError):
        return iso_str[:10] if len(iso_str) >= 10 else iso_str


def _load_project_knowledge() -> dict[str, dict]:
    """Load project knowledge from memory-bank files."""
    from .ingestor import discover_projects, read_project_knowledge

    projects = {}
    for project in discover_projects():
        if not project.has_memory_bank:
            continue
        knowledge = read_project_knowledge(project)
        if knowledge:
            projects[knowledge.name] = {
                "name": knowledge.name,
                "summary": knowledge.summary,
                "state": knowledge.current_state,
                "tech": knowledge.tech_stack,
                "capabilities": knowledge.capabilities,
                "next_steps": knowledge.next_steps,
                "files_read": knowledge.files_read,
            }
    return projects


_FILE_LOC_RE = re.compile(r'(?:at\s+)?(\w[\w./-]*\.\w+:\d+(?:-\d+)?)')


def _split_location(desc: str) -> tuple[str, str]:
    """Extract file:line reference from a description string.

    Returns (clean_description, file_location_or_empty).
    """
    m = _FILE_LOC_RE.search(desc)
    if m:
        loc = m.group(1)
        clean = desc[:m.start()].rstrip(' -–—at').strip()
        if not clean:
            clean = desc[m.end():].strip(' -–—').strip()
        if not clean:
            clean = desc
        return clean, loc
    return desc, ""


def _extract_per_project_counts(text: str) -> dict[str, dict[str, int]]:
    """Extract finding counts per project from an analysis.md JSON block.

    Returns {project_name: {"hidden": N, "dead": N, "insights": N, "connections": N, "total": N}}.
    """
    counts = {}
    json_match = re.search(r"---JSON_OUTPUT_START---\s*(.*?)\s*---JSON_OUTPUT_END---", text, re.DOTALL)
    if not json_match:
        return counts
    try:
        data = json.loads(json_match.group(1))
    except json.JSONDecodeError:
        return counts

    for proj in data.get("projects", []):
        name = proj.get("name", "")
        if not name:
            continue
        c = {
            "hidden": len(proj.get("hidden_capabilities", [])),
            "dead": len(proj.get("dead_code", [])),
            "insights": len(proj.get("insights", [])),
            "connections": len(proj.get("connections", [])),
        }
        c["total"] = sum(c.values())
        counts[name] = c
    return counts


def _load_gaps_actions() -> dict:
    """Load gaps action state from state/gaps_actions.json."""
    path = STATE_DIR / "gaps_actions.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, Exception):
            pass
    return {"actions": {}}


def _load_starred_proposals() -> dict:
    """Load starred proposals from state/starred_proposals.json."""
    path = STATE_DIR / "starred_proposals.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, Exception):
            pass
    return {"starred": {}}


def _extract_insights_from_analysis(text: str) -> dict:
    """Extract structured insights from an analysis.md."""
    result = {
        "hidden": [],
        "insights": [],
        "connections": [],
        "dead_code": [],
    }

    # Try JSON block first
    json_match = re.search(r"---JSON_OUTPUT_START---\s*(.*?)\s*---JSON_OUTPUT_END---", text, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(1))
            for proj in data.get("projects", []):
                result["hidden"].extend(proj.get("hidden_capabilities", []))
                result["insights"].extend(proj.get("insights", []))
                result["dead_code"].extend(proj.get("dead_code", []))
                for conn in proj.get("connections", []):
                    result["connections"].append(conn)
            for conn in data.get("cross_project_connections", []):
                result["connections"].append(conn)
            return result
        except json.JSONDecodeError:
            pass

    # Fall back to section extraction
    for line in text.split("\n"):
        line = line.strip()
        if not line.startswith("- ") and not line.startswith("* "):
            continue
        item = line[2:].strip()
        if not item or len(item) < 10:
            continue

        lower = item.lower()
        if any(w in lower for w in ["hidden", "unused", "orphan", "dead code"]):
            result["hidden"].append(item[:200])
        elif any(w in lower for w in ["connect", "integrat", "bridge", "combine"]):
            result["connections"].append({"description": item[:200]})
        elif any(w in lower for w in ["insight", "surprising", "notable", "finding"]):
            result["insights"].append(item[:200])

    return result


def _extract_proposals_from_json(text: str) -> list[dict]:
    """Extract dream proposals from JSON block in analysis text."""
    json_match = re.search(r"---JSON_OUTPUT_START---\s*(.*?)\s*---JSON_OUTPUT_END---", text, re.DOTALL)
    if not json_match:
        return []
    try:
        data = json.loads(json_match.group(1))
        return data.get("dream_proposals", [])
    except json.JSONDecodeError:
        return []


def _nav(active: str) -> str:
    pages = [
        ("index.html", "Home"),
        ("projects.html", "Projects"),
        ("connections.html", "Connections"),
        ("dreams.html", "Dreams"),
        ("gaps.html", "Tracker"),
    ]
    links = []
    for href, label in pages:
        cls = ' class="active"' if label.lower() == active.lower() else ""
        links.append(f'<a href="{href}"{cls}>{label}</a>')
    links.append('<a href="/admin" style="opacity:0.5">Admin</a>')

    return f"""<nav>
    <div class="container">
      <a href="index.html" class="nav-logo">
        <span class="spark">SPARK</span> DREAM
      </a>
      <div class="nav-links">
        {''.join(links)}
      </div>
    </div>
  </nav>"""


def _page(title: str, active: str, content: str, css_path: str = "style.css") -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Spark Dream | {_esc(title)}</title>
  <link rel="stylesheet" href="{css_path}">
</head>
<body>
  {_nav(active)}
  {content}
  <footer>
    <div class="container">
      <p>Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} by Spark Dream Engine</p>
    </div>
  </footer>
</body>
</html>"""


def _status_class(state: str) -> str:
    s = state.lower() if state else ""
    if any(w in s for w in ["active", "running", "complete", "working"]):
        return "status-active"
    if any(w in s for w in ["dormant", "paused", "inactive"]):
        return "status-dormant"
    if any(w in s for w in ["blocked", "error"]):
        return "status-blocked"
    return "status-dormant"


# ---------------------------------------------------------------------------
# Home page
# ---------------------------------------------------------------------------

def generate_index(projects: dict, analyses: dict, history: list,
                   dream_runs: dict) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    dreamed_count = len(analyses)
    total_analysis_chars = sum(len(a) for a in analyses.values())

    # Collect all insights from analyses
    all_insights = []
    all_hidden = []
    all_connections = []
    for name, text in analyses.items():
        extracted = _extract_insights_from_analysis(text)
        for i in extracted["insights"]:
            all_insights.append((name, i))
        for h in extracted["hidden"]:
            all_hidden.append((name, h))
        for c in extracted["connections"]:
            desc = c.get("description", c) if isinstance(c, dict) else str(c)
            all_connections.append(desc)

    stats_html = f"""
  <header class="hero">
    <div class="container">
      <h1>A Constellation of Making</h1>
      <p class="subtitle">
        {len(projects)} projects with memory-banks. {dreamed_count} deeply analyzed.
        {total_analysis_chars // 1000}k characters of source-code-level insights.
        Updated {now}.
      </p>
      <div class="stats-grid">
        <div class="stat-card">
          <div class="stat-value">{len(projects)}</div>
          <div class="stat-label">Projects Tracked</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">{dreamed_count}</div>
          <div class="stat-label">Deep Analyzed</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">{len(all_hidden)}</div>
          <div class="stat-label">Hidden Capabilities</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">{len(all_connections)}</div>
          <div class="stat-label">Connections Found</div>
        </div>
      </div>
    </div>
  </header>"""

    # Latest dream executive summary
    latest_summary_html = ""
    if dream_runs:
        latest_id = sorted(dream_runs.keys())[-1]
        latest_run = dream_runs[latest_id]
        # Find synthesis analysis
        synth_text = latest_run.get("synthesis", "")
        if synth_text:
            narrative = extract_narrative(synth_text)
            if narrative["executive_summary"]:
                # Render executive summary as HTML paragraphs
                exec_paras = _markdown_to_html(narrative["executive_summary"])
                latest_summary_html = f"""
  <section class="section">
    <div class="container">
      <h2>Latest Dream: {_esc(latest_id)}</h2>
      <div class="executive-summary">
        {exec_paras}
      </div>
      <a href="dream-{_esc(latest_id)}.html" class="btn-link">View full dream report &rarr;</a>
    </div>
  </section>"""

    # Top discoveries from latest dream
    discoveries_html = ""
    if dream_runs:
        latest_id = sorted(dream_runs.keys())[-1]
        synth_text = dream_runs[latest_id].get("synthesis", "")
        if synth_text:
            narrative = extract_narrative(synth_text)
            if narrative["discoveries"]:
                disc_items = ""
                for d in narrative["discoveries"][:5]:
                    loc = f'<span class="discovery-location">{_esc(d["location"])}</span>' if d.get("location") else ""
                    disc_items += f"""
                <div class="discovery-card">
                  <h4>{_esc(d["title"])}</h4>
                  {loc}
                  <p>{_esc(d["description"])}</p>
                </div>"""
                discoveries_html = f"""
  <section class="section">
    <div class="container">
      <h2>Surprising Discoveries</h2>
      <p class="section-subtitle">Hidden gems found by reading actual source code</p>
      <div class="discovery-grid">{disc_items}</div>
    </div>
  </section>"""

    # Top insights
    insights_html = ""
    if all_insights:
        items = "\n".join(
            f'<div class="insight-card"><span class="insight-project">{_esc(name)}</span> {_esc(insight)}</div>'
            for name, insight in all_insights[:10]
        )
        insights_html = f"""
  <section class="section">
    <div class="container">
      <h2>Top Insights</h2>
      <div class="insight-list">{items}</div>
    </div>
  </section>"""

    # Hidden capabilities
    hidden_html = ""
    if all_hidden:
        items = "\n".join(
            f'<div class="insight-card"><span class="insight-project">{_esc(name)}</span> {_esc(h)}</div>'
            for name, h in all_hidden[:10]
        )
        hidden_html = f"""
  <section class="section">
    <div class="container">
      <h2>Buried Treasure</h2>
      <p class="section-subtitle">Hidden capabilities found in source code</p>
      <div class="insight-list">{items}</div>
    </div>
  </section>"""

    # Dream history — with links to detail pages
    history_html = ""
    if history:
        rows = []
        for h in reversed(history[-10:]):
            dream_id = h['dream_id']
            targets = h.get("targets", list(h.get("clusters", {}).keys()))
            dur = f"{h['duration_sec']/60:.0f}m"
            link = f'<a href="dream-{_esc(dream_id)}.html">{_esc(dream_id)}</a>'
            mode_badge = f'<span class="badge badge-{h["mode"]}">{_esc(h["mode"])}</span>'
            rows.append(
                f"<tr><td>{link}</td><td>{mode_badge}</td>"
                f"<td>{_esc(', '.join(targets[:5]))}</td><td>{dur}</td></tr>"
            )
        history_html = f"""
  <section class="section">
    <div class="container">
      <h2>Dream History</h2>
      <table class="data-table">
        <thead><tr><th>Dream</th><th>Mode</th><th>Targets</th><th>Duration</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>
  </section>"""

    return _page("Constellation Analysis", "home",
                  stats_html + latest_summary_html + discoveries_html
                  + insights_html + hidden_html + history_html)


# ---------------------------------------------------------------------------
# Projects page (unchanged)
# ---------------------------------------------------------------------------

def generate_projects(projects: dict, analyses: dict, ingest_state: dict) -> str:
    cards = []
    for name in sorted(projects.keys()):
        p = projects[name]
        state = ingest_state.get(name, {})
        chunks = state.get("chunks", 0)
        has_analysis = name in analyses
        summary = _esc(p.get("summary", "")[:200])
        tech = _esc(p.get("tech", "")[:150])
        caps = p.get("capabilities", [])

        analysis_badge = '<span class="badge badge-accent">Deep Analyzed</span>' if has_analysis else ""
        caps_html = ""
        if caps:
            cap_items = "".join(f"<li>{_esc(c[:100])}</li>" for c in caps[:5])
            caps_html = f"<ul class='cap-list'>{cap_items}</ul>"

        last_ingested = _format_date(state.get("last_ingested", ""))
        date_html = f' &middot; {_esc(last_ingested)}' if last_ingested else ""

        cards.append(f"""
        <div class="project-card">
          <div class="project-header">
            <h3>{_esc(name)}</h3>
            {analysis_badge}
          </div>
          <p class="project-summary">{summary}</p>
          {f'<p class="project-tech">{tech}</p>' if tech else ''}
          {caps_html}
          <div class="project-meta">{chunks} memory chunks{date_html}</div>
        </div>""")

    content = f"""
  <header class="page-header">
    <div class="container">
      <h1>Projects</h1>
      <p class="subtitle">{len(projects)} projects with memory-banks</p>
    </div>
  </header>
  <section class="section">
    <div class="container">
      <div class="project-grid">{''.join(cards)}</div>
    </div>
  </section>"""

    return _page("Projects", "projects", content)


# ---------------------------------------------------------------------------
# Connections page (unchanged)
# ---------------------------------------------------------------------------

def generate_connections(analyses: dict) -> str:
    date_map = _build_analysis_date_map()
    all_connections = []
    for name, text in analyses.items():
        dream_date = date_map.get(name, "")
        extracted = _extract_insights_from_analysis(text)
        for conn in extracted["connections"]:
            if isinstance(conn, dict):
                all_connections.append({
                    "source": conn.get("source", name),
                    "target": conn.get("target", ""),
                    "description": conn.get("description", ""),
                    "dream": dream_date,
                })
            else:
                all_connections.append({
                    "source": name,
                    "target": "",
                    "description": str(conn)[:200],
                    "dream": dream_date,
                })

    rows = []
    for c in all_connections:
        dream_link = ""
        if c["dream"]:
            dream_link = f'<a href="dream-{_esc(c["dream"])}.html" class="conn-dream">{_esc(c["dream"][:10])}</a>'
        rows.append(
            f"<tr><td class='font-mono'>{_esc(c['source'])}</td>"
            f"<td class='font-mono'>{_esc(c['target'])}</td>"
            f"<td>{_esc(c['description'][:200])}</td>"
            f"<td>{dream_link}</td></tr>"
        )

    content = f"""
  <header class="page-header">
    <div class="container">
      <h1>Connections</h1>
      <p class="subtitle">{len(all_connections)} cross-project connections discovered</p>
    </div>
  </header>
  <section class="section">
    <div class="container">
      {f'''<table class="data-table">
        <thead><tr><th>Source</th><th>Target</th><th>Description</th><th>Dream</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>''' if rows else '<p class="text-muted">No connections found yet. Run a full dream to discover cross-project connections.</p>'}
    </div>
  </section>"""

    return _page("Connections", "connections", content)


# ---------------------------------------------------------------------------
# Dreams page — now with per-run summaries + proposals
# ---------------------------------------------------------------------------

def generate_dreams(dream_runs: dict, history: list) -> str:
    """Generate dreams landing page with summary stats and links."""
    # Count proposals
    all_proposals = set()
    for run in dream_runs.values():
        for sub_text in run.values():
            for p in _extract_proposals_from_json(sub_text):
                all_proposals.add(p.get("name", "").lower().strip())

    starred_count = len(_load_starred_proposals().get("starred", {}))
    latest_id = sorted(dream_runs.keys())[-1] if dream_runs else ""
    latest_mode = ""
    latest_dur = ""
    if history:
        latest_h = next((h for h in reversed(history) if h.get("dream_id") == latest_id), {})
        latest_mode = latest_h.get("mode", "")
        dur = latest_h.get("duration_sec", 0)
        latest_dur = f"{dur / 60:.0f} min" if dur else ""

    content = f"""
  <header class="hero">
    <div class="container">
      <h1>Dreams</h1>
      <p class="subtitle">AI-powered deep analysis of your codebase</p>
      <div class="stats-grid">
        <div class="stat-card">
          <div class="stat-value">{len(dream_runs)}</div>
          <div class="stat-label">Dream Runs</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">{len(all_proposals)}</div>
          <div class="stat-label">Proposals</div>
        </div>
        <div class="stat-card">
          <div class="stat-value stat-value-orange">{starred_count}</div>
          <div class="stat-label">Starred</div>
        </div>
        <div class="stat-card">
          <div class="stat-value stat-value-muted">{_esc(latest_id[:10])}</div>
          <div class="stat-label">Latest{f' ({latest_mode})' if latest_mode else ''}</div>
        </div>
      </div>
    </div>
  </header>
  <section class="section">
    <div class="container">
      <div class="action-grid" style="display:grid;grid-template-columns:1fr 1fr;gap:1.5rem;max-width:700px">
        <a href="proposals.html" class="run-card" style="text-decoration:none">
          <h3 style="margin:0 0 0.5rem;color:var(--text-primary)">Proposals</h3>
          <p style="margin:0;font-size:0.85rem;color:var(--text-secondary)">{len(all_proposals)} project ideas from deep analysis. Star your favorites, filter by project.</p>
        </a>
        <a href="runs.html" class="run-card" style="text-decoration:none">
          <h3 style="margin:0 0 0.5rem;color:var(--text-primary)">Dream Runs</h3>
          <p style="margin:0;font-size:0.85rem;color:var(--text-secondary)">{len(dream_runs)} runs with full reports. Latest: {_esc(latest_id[:10])}{f', {latest_dur}' if latest_dur else ''}.</p>
        </a>
      </div>
    </div>
  </section>"""

    return _page("Dreams", "dreams", content)


def generate_proposals(dream_runs: dict) -> str:
    """Generate standalone proposals page with stars and project filtering."""
    # Collect all proposals
    all_proposals = []
    for dream_id in reversed(sorted(dream_runs.keys())):
        run = dream_runs[dream_id]
        for sub_text in run.values():
            for p in _extract_proposals_from_json(sub_text):
                p["_dream_id"] = dream_id
                all_proposals.append(p)

    # Deduplicate by name (keep most recent)
    seen_names = set()
    unique_proposals = []
    for p in all_proposals:
        name_key = p.get("name", "").lower().strip()
        if name_key and name_key not in seen_names:
            seen_names.add(name_key)
            unique_proposals.append(p)

    # Load starred state
    starred_data = _load_starred_proposals()
    starred_set = set(starred_data.get("starred", {}).keys())

    cards = []
    if unique_proposals:
        # Sort: starred first, then by date desc
        def _prop_sort_key(p):
            pid = re.sub(r"[^a-z0-9-]", "-", p.get("name", "").lower().strip())
            is_starred = 0 if pid in starred_set else 1
            return (is_starred, p.get("_dream_id", ""))

        unique_proposals.sort(key=_prop_sort_key)

        for prop in unique_proposals:
            arch = _esc(prop.get("architecture", "")[:200])
            dream_id = prop.get("_dream_id", "")
            date_str = dream_id[:10] if dream_id else ""
            prop_id = re.sub(r"[^a-z0-9-]", "-", prop.get("name", "").lower().strip())
            is_starred = prop_id in starred_set
            star_class = "star-active" if is_starred else ""
            star_action = "false" if is_starred else "true"

            dream_link = f'<a href="dream-{_esc(dream_id)}.html" class="dream-date-link">{_esc(date_str)}</a>' if dream_id else ""

            # Clickable project tags
            combines_list = prop.get("combines", [])
            combines_data = " ".join(_esc(c.lower()) for c in combines_list)
            combines_tags = ""
            if combines_list:
                tags = "".join(
                    f'<span class="combines-tag" onclick="filterByProject(\'{_esc(c.lower())}\')">{_esc(c)}</span>'
                    for c in combines_list
                )
                combines_tags = f'<div class="dream-combines">{tags}</div>'

            cards.append(f"""
            <div class="dream-card {'dream-card-starred' if is_starred else ''}" data-proposal="{_esc(prop_id)}" data-combines="{combines_data}">
              <div class="dream-card-header">
                <h3>{_esc(prop.get('name', 'Untitled'))}</h3>
                <button class="btn-star {star_class}" onclick="starProposal('{_esc(prop_id)}',{star_action},this)" title="{'Unstar' if is_starred else 'Star as interesting'}">&#9733;</button>
              </div>
              <p>{_esc(prop.get('description', '')[:400])}</p>
              {combines_tags}
              {f'<div class="dream-arch">{arch}</div>' if arch else ''}
              <div class="dream-card-footer">
                {dream_link}
              </div>
            </div>""")

    star_js = """
  <script>
    async function starProposal(id, starred, btn) {
      try {
        await fetch('/api/proposals/star/' + id + '?starred=' + starred, {method: 'POST'});
        const card = btn.closest('.dream-card');
        if (starred) {
          btn.classList.add('star-active');
          card.classList.add('dream-card-starred');
          btn.title = 'Unstar';
          btn.setAttribute('onclick', "starProposal('" + id + "',false,this)");
        } else {
          btn.classList.remove('star-active');
          card.classList.remove('dream-card-starred');
          btn.title = 'Star as interesting';
          btn.setAttribute('onclick', "starProposal('" + id + "',true,this)");
        }
      } catch(e) { console.error('Failed:', e); }
    }

    function filterByProject(name) {
      const cards = document.querySelectorAll('#proposals-grid .dream-card');
      let shown = 0;
      cards.forEach(c => {
        const combines = (c.getAttribute('data-combines') || '').split(' ');
        if (combines.includes(name)) {
          c.style.display = '';
          shown++;
        } else {
          c.style.display = 'none';
        }
      });
      document.getElementById('filter-indicator').style.display = 'flex';
      document.getElementById('filter-name').textContent = name + ' (' + shown + ')';
      document.querySelectorAll('.combines-tag').forEach(t => {
        t.classList.toggle('combines-tag-active', t.textContent.toLowerCase() === name);
      });
    }

    function clearFilter() {
      document.querySelectorAll('#proposals-grid .dream-card').forEach(c => c.style.display = '');
      document.getElementById('filter-indicator').style.display = 'none';
      document.querySelectorAll('.combines-tag').forEach(t => t.classList.remove('combines-tag-active'));
    }
  </script>"""

    content = f"""
  <header class="page-header">
    <div class="container">
      <div class="dream-detail-meta">
        <a href="dreams.html" class="back-link">&larr; Dreams</a>
      </div>
      <h1>Proposals</h1>
      <p class="subtitle">{len(unique_proposals)} unique project ideas &middot; {len(starred_set)} starred</p>
    </div>
  </header>
  <section class="section">
    <div class="container">
      <div id="filter-indicator" class="filter-indicator" style="display:none">
        Filtering by: <strong id="filter-name"></strong>
        <button class="btn-action btn-dismiss" onclick="clearFilter()">Clear</button>
      </div>
      <div class="dream-grid" id="proposals-grid">{''.join(cards)}</div>
    </div>
  </section>
  {star_js}"""

    return _page("Proposals", "dreams", content)


def generate_runs(dream_runs: dict, history: list) -> str:
    """Generate standalone dream runs page."""
    run_cards = []
    for dream_id in reversed(sorted(dream_runs.keys())):
        run = dream_runs[dream_id]
        h_entry = next((h for h in history if h.get("dream_id") == dream_id), {})
        mode = h_entry.get("mode", "unknown")
        duration = h_entry.get("duration_sec", 0)
        dur_str = f"{duration / 60:.0f} min" if duration else ""

        analysis_count = len(run)
        total_chars = sum(len(t) for t in run.values())

        synth_text = run.get("synthesis", "")
        exec_summary = ""
        potential_count = 0
        discovery_count = 0
        if synth_text:
            narrative = extract_narrative(synth_text)
            if narrative["executive_summary"]:
                first_para = narrative["executive_summary"].split("\n\n")[0]
                exec_summary = _esc(first_para[:400])
            potential_count = len(narrative["potentials"])
            discovery_count = len(narrative["discoveries"])

        proposals = []
        for sub_text in run.values():
            proposals.extend(_extract_proposals_from_json(sub_text))

        mode_badge = f'<span class="badge badge-{mode}">{_esc(mode)}</span>'
        stats_line = f'{analysis_count} analyses &middot; {total_chars // 1000}k chars'
        if dur_str:
            stats_line += f' &middot; {dur_str}'
        if potential_count:
            stats_line += f' &middot; {potential_count} potentials'
        if discovery_count:
            stats_line += f' &middot; {discovery_count} discoveries'

        summary_html = f'<p class="run-summary">{exec_summary}</p>' if exec_summary else ""

        proposal_pills = ""
        if proposals:
            pills = "".join(
                f'<span class="proposal-pill">{_esc(p.get("name", "")[:50])}</span>'
                for p in proposals[:5]
            )
            proposal_pills = f'<div class="proposal-pills">{pills}</div>'

        run_cards.append(f"""
        <div class="run-card">
          <div class="run-header">
            <div>
              <h3><a href="dream-{_esc(dream_id)}.html">{_esc(dream_id)}</a></h3>
              <span class="run-stats">{stats_line}</span>
            </div>
            {mode_badge}
          </div>
          {summary_html}
          {proposal_pills}
          <a href="dream-{_esc(dream_id)}.html" class="btn-link">View full report &rarr;</a>
        </div>""")

    content = f"""
  <header class="page-header">
    <div class="container">
      <div class="dream-detail-meta">
        <a href="dreams.html" class="back-link">&larr; Dreams</a>
      </div>
      <h1>Dream Runs</h1>
      <p class="subtitle">{len(dream_runs)} runs</p>
    </div>
  </header>
  <section class="section">
    <div class="container">
      <div class="run-list">
        {''.join(run_cards) if run_cards else '<p class="text-muted">No dream runs yet.</p>'}
      </div>
    </div>
  </section>"""

    return _page("Dream Runs", "dreams", content)


# ---------------------------------------------------------------------------
# Dream detail page — full report for one dream run
# ---------------------------------------------------------------------------

def generate_dream_detail(dream_id: str, run: dict[str, str],
                          history_entry: dict) -> str:
    """Generate a detail page for a single dream run."""
    mode = history_entry.get("mode", "unknown")
    duration = history_entry.get("duration_sec", 0)
    started = history_entry.get("started_at", "")

    # Collect narratives from all sub-analyses
    synth_text = run.get("synthesis", "")
    synth_narrative = extract_narrative(synth_text) if synth_text else None

    # Header with metadata
    dur_str = f"{duration / 60:.0f} min" if duration else "unknown"
    sub_count = len(run)
    total_chars = sum(len(t) for t in run.values())

    header = f"""
  <header class="page-header">
    <div class="container">
      <div class="dream-detail-meta">
        <a href="dreams.html" class="back-link">&larr; All Dreams</a>
        <span class="badge badge-{mode}">{_esc(mode)}</span>
      </div>
      <h1>Dream: {_esc(dream_id)}</h1>
      <p class="subtitle">
        {sub_count} analyses &middot; {total_chars // 1000}k chars &middot; {dur_str}
        {f' &middot; started {_esc(started[:19])}' if started else ''}
      </p>
    </div>
  </header>"""

    sections = []

    # Executive Summary
    if synth_narrative and synth_narrative["executive_summary"]:
        paras = _markdown_to_html(synth_narrative["executive_summary"])
        sections.append(f"""
  <section class="section">
    <div class="container">
      <h2>Executive Summary</h2>
      <div class="executive-summary">{paras}</div>
    </div>
  </section>""")

    # Architecture Diagram
    if synth_narrative and synth_narrative["architecture_diagram"]:
        diagram = _esc(synth_narrative["architecture_diagram"])
        sections.append(f"""
  <section class="section">
    <div class="container">
      <h2>Architecture Map</h2>
      <div class="ascii-block">{diagram}</div>
    </div>
  </section>""")

    # Top Unrealized Potentials
    if synth_narrative and synth_narrative["potentials"]:
        pot_items = ""
        for i, p in enumerate(synth_narrative["potentials"], 1):
            impact_badge = f'<span class="tag tag-impact">{_esc(p["impact"])}</span>' if p.get("impact") else ""
            components = f'<p class="potential-components">{_esc(p["components"])}</p>' if p.get("components") else ""
            pot_items += f"""
            <div class="potential-card">
              <div class="potential-header">
                <h4><span class="potential-num">{i}.</span> {_esc(p["name"])}</h4>
                {impact_badge}
              </div>
              {components}
              <p>{_esc(p["description"])}</p>
            </div>"""
        sections.append(f"""
  <section class="section">
    <div class="container">
      <h2>Top Unrealized Potentials</h2>
      <div class="potential-list">{pot_items}</div>
    </div>
  </section>""")

    # Surprising Discoveries
    if synth_narrative and synth_narrative["discoveries"]:
        disc_items = ""
        for d in synth_narrative["discoveries"]:
            loc = f'<span class="discovery-location">{_esc(d["location"])}</span>' if d.get("location") else ""
            disc_items += f"""
            <div class="discovery-card">
              <h4>{_esc(d["title"])}</h4>
              {loc}
              <p>{_esc(d["description"])}</p>
            </div>"""
        sections.append(f"""
  <section class="section">
    <div class="container">
      <h2>Surprising Discoveries</h2>
      <p class="section-subtitle">Hidden gems found by reading actual source code</p>
      <div class="discovery-grid">{disc_items}</div>
    </div>
  </section>""")

    # Dream Proposals from this run
    proposals = []
    for sub_text in run.values():
        proposals.extend(_extract_proposals_from_json(sub_text))

    if proposals:
        # Deduplicate
        seen = set()
        unique = []
        for p in proposals:
            key = p.get("name", "").lower().strip()
            if key and key not in seen:
                seen.add(key)
                unique.append(p)

        prop_cards = ""
        for prop in unique:
            combines = ", ".join(prop.get("combines", []))
            arch = prop.get("architecture", "")
            prop_cards += f"""
            <div class="dream-card">
              <h3>{_esc(prop.get('name', 'Untitled'))}</h3>
              <p>{_esc(prop.get('description', '')[:500])}</p>
              {f'<p class="dream-combines">Combines: {_esc(combines)}</p>' if combines else ''}
              {f'<div class="dream-arch">{_esc(arch[:300])}</div>' if arch else ''}
            </div>"""

        sections.append(f"""
  <section class="section">
    <div class="container">
      <h2>Dream Proposals</h2>
      <p class="section-subtitle">{len(unique)} new project ideas from this dream</p>
      <div class="dream-grid">{prop_cards}</div>
    </div>
  </section>""")

    # Shared Infrastructure Patterns
    if synth_narrative and synth_narrative["shared_patterns"]:
        table_html = _markdown_table_to_html(synth_narrative["shared_patterns"])
        sections.append(f"""
  <section class="section">
    <div class="container">
      <h2>Shared Infrastructure Patterns</h2>
      {table_html}
    </div>
  </section>""")

    # Recommendations
    if synth_narrative:
        recs = synth_narrative["recommendations"]
        has_recs = any(recs.values())
        if has_recs:
            rec_html = ""
            for label, key, css_class in [
                ("Immediate (High Impact, Low Effort)", "immediate", "rec-immediate"),
                ("Short-term (Medium Impact)", "short_term", "rec-short"),
                ("Long-term (High Impact, High Effort)", "long_term", "rec-long"),
            ]:
                items = recs.get(key, [])
                if items:
                    li = "".join(f"<li>{_esc(r)}</li>" for r in items)
                    rec_html += f"""
                <div class="rec-group {css_class}">
                  <h4>{label}</h4>
                  <ol>{li}</ol>
                </div>"""
            sections.append(f"""
  <section class="section">
    <div class="container">
      <h2>Recommendations</h2>
      <div class="rec-grid">{rec_html}</div>
    </div>
  </section>""")

    # Cluster analyses summary
    cluster_items = ""
    for sub_name in sorted(run.keys()):
        if sub_name == "synthesis":
            continue
        sub_text = run[sub_name]
        sub_narrative = extract_narrative(sub_text)
        exec_para = ""
        if sub_narrative["executive_summary"]:
            exec_para = _esc(sub_narrative["executive_summary"].split("\n\n")[0][:300])

        # Count projects in this cluster's JSON
        proj_count = 0
        json_match = re.search(r"---JSON_OUTPUT_START---\s*(.*?)\s*---JSON_OUTPUT_END---", sub_text, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group(1))
                proj_count = len(data.get("projects", []))
            except json.JSONDecodeError:
                pass

        cluster_items += f"""
        <div class="cluster-card">
          <h4>{_esc(sub_name)}</h4>
          <span class="cluster-meta">{proj_count} projects &middot; {len(sub_text) // 1000}k chars</span>
          {f'<p>{exec_para}</p>' if exec_para else ''}
        </div>"""

    if cluster_items:
        sections.append(f"""
  <section class="section">
    <div class="container">
      <h2>Cluster Analyses</h2>
      <div class="cluster-grid">{cluster_items}</div>
    </div>
  </section>""")

    return _page(f"Dream: {dream_id}", "dreams",
                  header + "".join(sections))


# ---------------------------------------------------------------------------
# Gaps page (unchanged)
# ---------------------------------------------------------------------------

def generate_gaps(projects: dict, analyses: dict, dream_runs: dict) -> str:
    """Generate the 'What To Do Next' tracker page."""
    from collections import defaultdict

    actions = _load_gaps_actions()
    acted = actions.get("actions", {})

    # --- Find latest synthesis for curated recommendations ---
    latest_narrative = None
    latest_run_id = ""
    for run_id in reversed(sorted(dream_runs.keys())):
        synth = dream_runs[run_id].get("synthesis", "")
        if synth:
            latest_narrative = extract_narrative(synth)
            latest_run_id = run_id
            break

    # --- Section 1: Recommendations (actionable) ---
    recs_html = ""
    resolved_items = []
    if latest_narrative:
        recs = latest_narrative["recommendations"]
        groups_html = ""
        for label, key, css_class in [
            ("Do Today", "immediate", "rec-immediate"),
            ("This Week", "short_term", "rec-short"),
            ("On The Horizon", "long_term", "rec-long"),
        ]:
            items = recs.get(key, [])
            if not items:
                continue
            li_html = ""
            for i, item in enumerate(items):
                item_id = f"rec-{key}-{i}"
                action_state = acted.get(item_id, {})
                status = action_state.get("status", "")

                if status in ("done", "dismissed"):
                    resolved_items.append((item_id, item, status))
                    continue

                li_html += f"""
                <li class="action-item" data-id="{item_id}">
                  <span class="action-text">{_esc(item)}</span>
                  <span class="action-buttons">
                    <button class="btn-action btn-done" onclick="markAction('{item_id}','done',this)">Done</button>
                    <button class="btn-action btn-dismiss" onclick="markAction('{item_id}','dismissed',this)">Dismiss</button>
                  </span>
                </li>"""

            if li_html:
                groups_html += f"""
            <div class="rec-group {css_class}">
              <h4>{label}</h4>
              <ul class="action-list">{li_html}</ul>
            </div>"""

        # Resolved items section
        resolved_html = ""
        if resolved_items:
            resolved_li = ""
            for item_id, item, status in resolved_items:
                css = "is-done" if status == "done" else "is-dismissed"
                resolved_li += f"""
                <li class="action-item {css}" data-id="{item_id}">
                  <span class="action-text">{_esc(item)}</span>
                  <span class="action-buttons">
                    <button class="btn-action btn-undo" onclick="markAction('{item_id}','open',this)">Undo</button>
                  </span>
                </li>"""
            resolved_html = f"""
            <details class="resolved-group">
              <summary>Resolved ({len(resolved_items)} items)</summary>
              <ul class="action-list">{resolved_li}</ul>
            </details>"""

        if groups_html or resolved_html:
            recs_html = f"""
  <section class="section">
    <div class="container">
      <h2>Recommendations</h2>
      <p class="section-subtitle">From latest synthesis ({_esc(latest_run_id)})</p>
      {groups_html}
      {resolved_html}
    </div>
  </section>"""

    # --- Section 2: Finding Trends ---
    # Build per-run, per-project counts
    # {project_name: {dream_id: total_count}}
    trend_data: dict[str, dict[str, int]] = defaultdict(dict)
    run_ids = sorted(dream_runs.keys())

    for run_id in run_ids:
        run = dream_runs[run_id]
        for sub_name, sub_text in run.items():
            counts = _extract_per_project_counts(sub_text)
            for proj_name, c in counts.items():
                # Accumulate across sub-analyses within a run
                prev = trend_data[proj_name].get(run_id, 0)
                trend_data[proj_name][run_id] = prev + c["total"]

    # Sort projects by latest count descending
    def _latest_count(proj):
        for rid in reversed(run_ids):
            if rid in trend_data[proj]:
                return trend_data[proj][rid]
        return 0

    sorted_projects = sorted(trend_data.keys(), key=_latest_count, reverse=True)

    # Build trends table (top 15 projects)
    trends_html = ""
    if sorted_projects and len(run_ids) >= 2:
        # Column headers — use short date labels
        th_cols = ""
        for rid in run_ids[-5:]:  # Show last 5 runs
            short = rid[:10]
            th_cols += f"<th>{_esc(short)}</th>"

        rows = ""
        for proj in sorted_projects[:15]:
            cells = ""
            values = []
            for rid in run_ids[-5:]:
                val = trend_data[proj].get(rid)
                if val is not None:
                    cells += f"<td>{val}</td>"
                    values.append(val)
                else:
                    cells += "<td class='text-muted'>—</td>"

            # Trend indicator
            trend_cell = "<td class='trend-flat'>—</td>"
            if len(values) >= 2:
                delta = values[-1] - values[-2]
                if delta > 0:
                    trend_cell = f"<td class='trend-up'>+{delta} &uarr;</td>"
                elif delta < 0:
                    trend_cell = f"<td class='trend-down'>{delta} &darr;</td>"
                else:
                    trend_cell = "<td class='trend-flat'>0 =</td>"
            elif len(values) == 1:
                trend_cell = "<td class='trend-new'>new</td>"

            rows += f"<tr><td class='font-mono'>{_esc(proj)}</td>{cells}{trend_cell}</tr>"

        trends_html = f"""
  <section class="section">
    <div class="container">
      <h2>Finding Trends</h2>
      <p class="section-subtitle">Per-project finding counts across dream runs</p>
      <div style="overflow-x:auto">
        <table class="data-table trends-table">
          <thead><tr><th>Project</th>{th_cols}<th>Trend</th></tr></thead>
          <tbody>{rows}</tbody>
        </table>
      </div>
    </div>
  </section>"""

    # --- Section 3: Newly Discovered ---
    newly_html = ""
    if len(run_ids) >= 2:
        latest_id = run_ids[-1]
        previous_ids = set(run_ids[:-1])

        # Projects in latest run
        latest_projects = set()
        for sub_text in dream_runs[latest_id].values():
            latest_projects.update(_extract_per_project_counts(sub_text).keys())

        # Projects in all previous runs
        prev_projects = set()
        for rid in previous_ids:
            for sub_text in dream_runs[rid].values():
                prev_projects.update(_extract_per_project_counts(sub_text).keys())

        new_projects = latest_projects - prev_projects
        if new_projects:
            cards = ""
            for proj in sorted(new_projects):
                counts = trend_data[proj].get(latest_id, 0)
                cards += f"""
                <div class="cluster-card">
                  <h4>{_esc(proj)}</h4>
                  <span class="badge badge-accent">First analysis</span>
                  <span class="cluster-meta">{counts} findings</span>
                </div>"""
            newly_html = f"""
  <section class="section">
    <div class="container">
      <h2>Newly Discovered</h2>
      <p class="section-subtitle">Projects analyzed for the first time in the latest run</p>
      <div class="cluster-grid">{cards}</div>
    </div>
  </section>"""

    # --- Section 4: Unanalyzed Projects ---
    unanalyzed = [name for name in projects if name not in analyses]
    unanalyzed_html = ""
    if unanalyzed:
        cards = ""
        for name in sorted(unanalyzed):
            cards += f"""
            <div class="cluster-card">
              <h4>{_esc(name)}</h4>
              <span class="cluster-meta">Not yet analyzed</span>
            </div>"""
        unanalyzed_html = f"""
  <section class="section">
    <div class="container">
      <h2>Unanalyzed Projects</h2>
      <p class="section-subtitle">Projects with memory-banks but no dream analysis yet</p>
      <div class="cluster-grid">{cards}</div>
    </div>
  </section>"""

    # --- Section 5: Full Catalog (collapsed) ---
    date_map = _build_analysis_date_map()
    all_hidden = []
    all_dead = []
    for name, text in analyses.items():
        dream_date = date_map.get(name, "")
        extracted = _extract_insights_from_analysis(text)
        for h in extracted["hidden"]:
            all_hidden.append((name, h, dream_date))
        for d in extracted["dead_code"]:
            all_dead.append((name, d, dream_date))
    all_hidden.sort(key=lambda x: x[2], reverse=True)
    all_dead.sort(key=lambda x: x[2], reverse=True)

    total_findings = len(all_hidden) + len(all_dead)
    catalog_html = ""
    if total_findings:
        hidden_rows = "".join(
            f"<tr><td class='font-mono'>{_esc(n)}</td><td>{_esc(d[:200])}</td><td class='text-muted'>{_esc(dt[:10])}</td></tr>"
            for n, d, dt in all_hidden[:50]
        )
        dead_rows = "".join(
            f"<tr><td class='font-mono'>{_esc(n)}</td><td>{_esc(d[:200])}</td><td class='text-muted'>{_esc(dt[:10])}</td></tr>"
            for n, d, dt in all_dead[:50]
        )

        catalog_inner = ""
        if hidden_rows:
            catalog_inner += f"""
            <h3>Hidden Capabilities (top 50 of {len(all_hidden)})</h3>
            <table class="data-table"><thead><tr><th>Project</th><th>Capability</th><th>Found</th></tr></thead>
            <tbody>{hidden_rows}</tbody></table>"""
        if dead_rows:
            catalog_inner += f"""
            <h3 style="margin-top:2rem">Dead Code (top 50 of {len(all_dead)})</h3>
            <table class="data-table"><thead><tr><th>Project</th><th>Description</th><th>Found</th></tr></thead>
            <tbody>{dead_rows}</tbody></table>"""

        catalog_html = f"""
  <section class="section">
    <div class="container">
      <details class="catalog-toggle">
        <summary>Full Catalog ({total_findings} findings)</summary>
        <div class="catalog-body">{catalog_inner}</div>
      </details>
    </div>
  </section>"""

    # --- Inline JS for action buttons ---
    action_js = """
  <script>
    async function markAction(id, status, btn) {
      try {
        await fetch('/api/gaps/actions/' + id + '?status=' + status, {method: 'POST'});
        const item = btn.closest('.action-item');
        if (status === 'open') {
          item.classList.remove('is-done', 'is-dismissed');
        } else {
          item.classList.add(status === 'done' ? 'is-done' : 'is-dismissed');
        }
        // Hide the action buttons, show undo
        const btns = item.querySelector('.action-buttons');
        if (status === 'open') {
          btns.innerHTML = '<button class="btn-action btn-done" onclick="markAction(\\'' + id + '\\',\\'done\\',this)">Done</button><button class="btn-action btn-dismiss" onclick="markAction(\\'' + id + '\\',\\'dismissed\\',this)">Dismiss</button>';
        } else {
          btns.innerHTML = '<button class="btn-action btn-undo" onclick="markAction(\\'' + id + '\\',\\'open\\',this)">Undo</button>';
        }
      } catch(e) { console.error('Failed:', e); }
    }
  </script>"""

    no_synthesis = ""
    if not latest_narrative:
        no_synthesis = """
  <section class="section">
    <div class="container">
      <p class="text-muted">Run a full dream with synthesis to generate recommendations.</p>
    </div>
  </section>"""

    content = f"""
  <header class="page-header">
    <div class="container">
      <h1>What To Do Next</h1>
      <p class="subtitle">Curated recommendations, trends across dream runs, and action tracking</p>
    </div>
  </header>
  {no_synthesis}
  {recs_html}
  {trends_html}
  {newly_html}
  {unanalyzed_html}
  {catalog_html}
  {action_js}"""

    return _page("What To Do Next", "gaps", content)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _markdown_to_html(text: str) -> str:
    """Convert simple markdown to HTML (paragraphs, bold, inline code, lists)."""
    if not text:
        return ""

    lines = text.strip().split("\n")
    html_parts = []
    in_list = False

    for line in lines:
        stripped = line.strip()

        # Headings
        if stripped.startswith("### "):
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            html_parts.append(f"<h4>{_inline_md(_esc(stripped[4:]))}</h4>")
            continue

        # List items
        if stripped.startswith("- ") or stripped.startswith("* "):
            if not in_list:
                html_parts.append("<ul>")
                in_list = True
            html_parts.append(f"<li>{_inline_md(_esc(stripped[2:]))}</li>")
            continue

        # Numbered list items
        num_match = re.match(r"^\d+\.\s+(.+)", stripped)
        if num_match:
            if not in_list:
                html_parts.append("<ol>")
                in_list = True
            html_parts.append(f"<li>{_inline_md(_esc(num_match.group(1)))}</li>")
            continue

        # Close list
        if in_list and not stripped:
            html_parts.append("</ul>" if html_parts[-1] != "<ol>" else "</ol>")
            in_list = False
            continue

        # Paragraph
        if stripped:
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            html_parts.append(f"<p>{_inline_md(_esc(stripped))}</p>")

    if in_list:
        html_parts.append("</ul>")

    return "\n".join(html_parts)


def _inline_md(text: str) -> str:
    """Convert inline markdown (bold, code) in already-escaped text."""
    # Bold: **text**
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    # Inline code: `text`
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    return text


def _markdown_table_to_html(table_text: str) -> str:
    """Convert a markdown table to HTML."""
    lines = [l.strip() for l in table_text.strip().split("\n") if l.strip().startswith("|")]
    if len(lines) < 2:
        return f"<pre>{_esc(table_text)}</pre>"

    # Header row
    headers = [c.strip() for c in lines[0].strip("|").split("|")]
    th = "".join(f"<th>{_esc(h)}</th>" for h in headers)

    # Data rows (skip separator line)
    rows_html = ""
    for line in lines[2:]:
        cells = [c.strip() for c in line.strip("|").split("|")]
        td = "".join(f"<td>{_esc(c)}</td>" for c in cells)
        rows_html += f"<tr>{td}</tr>"

    return f"""<table class="data-table">
      <thead><tr>{th}</tr></thead>
      <tbody>{rows_html}</tbody>
    </table>"""


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def regenerate_site():
    """Regenerate all HTML pages from current data."""
    logger.info("Regenerating dream site...")

    SITE_DIR.mkdir(parents=True, exist_ok=True)

    projects = _load_project_knowledge()
    analyses = _load_all_analyses()
    history = _load_dream_history()
    ingest_state = _load_ingest_state()
    dream_runs = _load_dream_run_analyses()

    # Generate main pages
    (SITE_DIR / "index.html").write_text(
        generate_index(projects, analyses, history, dream_runs))
    (SITE_DIR / "projects.html").write_text(
        generate_projects(projects, analyses, ingest_state))
    (SITE_DIR / "connections.html").write_text(
        generate_connections(analyses))
    (SITE_DIR / "dreams.html").write_text(
        generate_dreams(dream_runs, history))
    (SITE_DIR / "proposals.html").write_text(
        generate_proposals(dream_runs))
    (SITE_DIR / "runs.html").write_text(
        generate_runs(dream_runs, history))
    (SITE_DIR / "gaps.html").write_text(
        generate_gaps(projects, analyses, dream_runs))

    # Generate dream detail pages
    detail_count = 0
    for dream_id, run in dream_runs.items():
        h_entry = next((h for h in history if h.get("dream_id") == dream_id), {})
        page_html = generate_dream_detail(dream_id, run, h_entry)
        (SITE_DIR / f"dream-{dream_id}.html").write_text(page_html)
        detail_count += 1

    # Copy style.css from template if not present, or always update
    css_dest = SITE_DIR / "style.css"
    css_src = TEMPLATE_DIR / "style.css"
    if css_src.exists():
        shutil.copy2(css_src, css_dest)

    logger.info(
        f"Site regenerated: {len(projects)} projects, {len(analyses)} analyses, "
        f"{len(history)} dream runs, {detail_count} detail pages"
    )
