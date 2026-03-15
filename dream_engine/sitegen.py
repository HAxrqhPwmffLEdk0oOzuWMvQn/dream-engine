"""Site generator — reads dream outputs + ingested knowledge, regenerates HTML pages.

Keeps the existing style.css and nav structure. Replaces page content with
current data from dream analyses, ingest state, and Milady memory.
"""

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from .config import settings

logger = logging.getLogger(__name__)

SITE_DIR = Path(__file__).parent.parent / "site"
DREAMS_DIR = settings.dreams_dir
STATE_DIR = settings.state_dir


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


def _nav(active: str) -> str:
    pages = [
        ("index.html", "Home"),
        ("projects.html", "Projects"),
        ("connections.html", "Connections"),
        ("dreams.html", "Dreams"),
        ("gaps.html", "Gaps"),
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


def _page(title: str, active: str, content: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Spark Dream | {title}</title>
  <link rel="stylesheet" href="style.css">
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


def generate_index(projects: dict, analyses: dict, history: list) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    dreamed_count = len(analyses)
    total_analysis_lines = sum(a.count("\n") for a in analyses.values())

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
        {total_analysis_lines:,} lines of source-code-level insights.
        Updated {now}.
      </p>
      <div class="stats-grid">
        <div class="stat-card">
          <div class="stat-number">{len(projects)}</div>
          <div class="stat-label">Projects Tracked</div>
        </div>
        <div class="stat-card">
          <div class="stat-number">{dreamed_count}</div>
          <div class="stat-label">Deep Analyzed</div>
        </div>
        <div class="stat-card">
          <div class="stat-number">{len(all_hidden)}</div>
          <div class="stat-label">Hidden Capabilities</div>
        </div>
        <div class="stat-card">
          <div class="stat-number">{len(all_connections)}</div>
          <div class="stat-label">Connections Found</div>
        </div>
      </div>
    </div>
  </header>"""

    # Top insights
    insights_html = ""
    if all_insights:
        items = "\n".join(
            f'<div class="insight-card"><span class="insight-project">{name}</span> {insight}</div>'
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
            f'<div class="insight-card"><span class="insight-project">{name}</span> {h}</div>'
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

    # Dream history
    history_html = ""
    if history:
        rows = []
        for h in reversed(history[-5:]):
            targets = h.get("targets", list(h.get("clusters", {}).keys()))
            dur = f"{h['duration_sec']/60:.0f}m"
            rows.append(
                f"<tr><td>{h['dream_id']}</td><td>{h['mode']}</td>"
                f"<td>{', '.join(targets[:4])}</td><td>{dur}</td></tr>"
            )
        history_html = f"""
  <section class="section">
    <div class="container">
      <h2>Dream History</h2>
      <table class="data-table">
        <thead><tr><th>ID</th><th>Mode</th><th>Targets</th><th>Duration</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>
  </section>"""

    return _page("Constellation Analysis", "home",
                  stats_html + insights_html + hidden_html + history_html)


def generate_projects(projects: dict, analyses: dict, ingest_state: dict) -> str:
    cards = []
    for name in sorted(projects.keys()):
        p = projects[name]
        state = ingest_state.get(name, {})
        chunks = state.get("chunks", 0)
        has_analysis = name in analyses
        summary = p.get("summary", "")[:200]
        tech = p.get("tech", "")[:150]
        caps = p.get("capabilities", [])

        analysis_badge = '<span class="badge badge-accent">Deep Analyzed</span>' if has_analysis else ""
        caps_html = ""
        if caps:
            cap_items = "".join(f"<li>{c[:100]}</li>" for c in caps[:5])
            caps_html = f"<ul class='cap-list'>{cap_items}</ul>"

        cards.append(f"""
        <div class="project-card">
          <div class="project-header">
            <h3>{name}</h3>
            {analysis_badge}
          </div>
          <p class="project-summary">{summary}</p>
          {f'<p class="project-tech">{tech[:100]}</p>' if tech else ''}
          {caps_html}
          <div class="project-meta">{chunks} memory chunks</div>
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


def generate_connections(analyses: dict) -> str:
    all_connections = []
    for name, text in analyses.items():
        extracted = _extract_insights_from_analysis(text)
        for conn in extracted["connections"]:
            if isinstance(conn, dict):
                all_connections.append({
                    "source": conn.get("source", name),
                    "target": conn.get("target", ""),
                    "description": conn.get("description", ""),
                })
            else:
                all_connections.append({
                    "source": name,
                    "target": "",
                    "description": str(conn)[:200],
                })

    rows = []
    for c in all_connections:
        rows.append(
            f"<tr><td class='font-mono'>{c['source']}</td>"
            f"<td class='font-mono'>{c['target']}</td>"
            f"<td>{c['description'][:200]}</td></tr>"
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
        <thead><tr><th>Source</th><th>Target</th><th>Description</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>''' if rows else '<p class="text-muted">No connections found yet. Run a full dream to discover cross-project connections.</p>'}
    </div>
  </section>"""

    return _page("Connections", "connections", content)


def generate_dreams(analyses: dict) -> str:
    """Generate the dreams/proposals page from analysis outputs."""
    proposals = []
    for name, text in analyses.items():
        json_match = re.search(r"---JSON_OUTPUT_START---\s*(.*?)\s*---JSON_OUTPUT_END---", text, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group(1))
                for prop in data.get("dream_proposals", []):
                    proposals.append(prop)
            except json.JSONDecodeError:
                pass

    cards = []
    for prop in proposals:
        combines = ", ".join(prop.get("combines", []))
        cards.append(f"""
        <div class="dream-card">
          <h3>{prop.get('name', 'Untitled')}</h3>
          <p>{prop.get('description', '')[:400]}</p>
          {f'<p class="dream-combines">Combines: {combines}</p>' if combines else ''}
          {f'<p class="dream-arch">{prop.get("architecture", "")[:200]}</p>' if prop.get("architecture") else ''}
        </div>""")

    content = f"""
  <header class="page-header">
    <div class="container">
      <h1>Dreams</h1>
      <p class="subtitle">{len(proposals)} project proposals from deep analysis</p>
    </div>
  </header>
  <section class="section">
    <div class="container">
      <div class="dream-grid">
        {''.join(cards) if cards else '<p class="text-muted">No dream proposals yet. Run a full dream with synthesis to generate new project ideas.</p>'}
      </div>
    </div>
  </section>"""

    return _page("Dreams", "dreams", content)


def generate_gaps(projects: dict, analyses: dict) -> str:
    """Generate the gaps/unrealized potential page."""
    all_hidden = []
    all_dead = []

    for name, text in analyses.items():
        extracted = _extract_insights_from_analysis(text)
        for h in extracted["hidden"]:
            all_hidden.append((name, h))
        for d in extracted["dead_code"]:
            all_dead.append((name, d))

    # Also find projects with no analysis
    unanalyzed = [name for name in projects if name not in analyses]

    hidden_rows = "".join(
        f"<tr><td class='font-mono'>{name}</td><td>{desc[:200]}</td></tr>"
        for name, desc in all_hidden
    )
    dead_rows = "".join(
        f"<tr><td class='font-mono'>{name}</td><td>{desc[:200]}</td></tr>"
        for name, desc in all_dead
    )
    unanalyzed_items = "".join(f"<li>{name}</li>" for name in sorted(unanalyzed))

    content = f"""
  <header class="page-header">
    <div class="container">
      <h1>Gaps &amp; Unrealized Potential</h1>
      <p class="subtitle">{len(all_hidden)} hidden capabilities, {len(all_dead)} dead code findings, {len(unanalyzed)} unanalyzed projects</p>
    </div>
  </header>
  <section class="section">
    <div class="container">
      <h2>Hidden Capabilities</h2>
      <p class="section-subtitle">Code that exists but isn't being used to its full potential</p>
      {f'<table class="data-table"><thead><tr><th>Project</th><th>Capability</th></tr></thead><tbody>{hidden_rows}</tbody></table>' if hidden_rows else '<p class="text-muted">Run deep dreams to discover hidden capabilities.</p>'}
    </div>
  </section>
  <section class="section">
    <div class="container">
      <h2>Dead Code Worth Reviving</h2>
      {f'<table class="data-table"><thead><tr><th>Project</th><th>Description</th></tr></thead><tbody>{dead_rows}</tbody></table>' if dead_rows else '<p class="text-muted">No dead code findings yet.</p>'}
    </div>
  </section>
  <section class="section">
    <div class="container">
      <h2>Not Yet Analyzed</h2>
      <p class="section-subtitle">These projects have memory-banks but no deep dream analysis yet</p>
      {f'<ul class="unanalyzed-list">{unanalyzed_items}</ul>' if unanalyzed_items else '<p class="text-muted">All projects have been analyzed!</p>'}
    </div>
  </section>"""

    return _page("Gaps", "gaps", content)


def regenerate_site():
    """Regenerate all HTML pages from current data."""
    logger.info("Regenerating dream site...")

    SITE_DIR.mkdir(parents=True, exist_ok=True)

    projects = _load_project_knowledge()
    analyses = _load_all_analyses()
    history = _load_dream_history()
    ingest_state = _load_ingest_state()

    # Generate pages
    (SITE_DIR / "index.html").write_text(generate_index(projects, analyses, history))
    (SITE_DIR / "projects.html").write_text(generate_projects(projects, analyses, ingest_state))
    (SITE_DIR / "connections.html").write_text(generate_connections(analyses))
    (SITE_DIR / "dreams.html").write_text(generate_dreams(analyses))
    (SITE_DIR / "gaps.html").write_text(generate_gaps(projects, analyses))

    # Don't touch style.css — keep the existing one

    logger.info(
        f"Site regenerated: {len(projects)} projects, {len(analyses)} analyses, "
        f"{len(history)} dream runs"
    )
