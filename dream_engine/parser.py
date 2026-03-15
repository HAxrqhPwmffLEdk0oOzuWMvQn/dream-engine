"""Parse dream markdown output into structured data when JSON block is unavailable.

Falls back to regex-based extraction from markdown analysis files.
"""

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


def parse_dream_markdown(text: str) -> dict:
    """Parse a dream analysis markdown file into structured project data.

    Handles two formats:
    1. Per-project headers (## Project: X)
    2. Section-based analysis (## Surprising Findings, ## Cross-Project Connections, etc.)

    Returns dict matching the JSON output schema from spawner.py.
    """
    if not text:
        return {"projects": [], "cross_project_connections": [], "dream_proposals": []}

    projects = []
    connections = []
    proposals = []

    # Try per-project split first
    project_blocks = re.split(
        r"^##\s+(?:Project:\s*|(?:\d+\.?\s+))?(\S+.*?)$",
        text,
        flags=re.MULTILINE,
    )

    if len(project_blocks) > 2:
        for i in range(1, len(project_blocks) - 1, 2):
            name = project_blocks[i].strip().rstrip(" —-:").lower()
            name = re.sub(r"[^a-z0-9_-]", "-", name).strip("-")
            content = project_blocks[i + 1] if i + 1 < len(project_blocks) else ""

            if not name or len(name) < 2 or len(name) > 60:
                continue

            project = {
                "name": name,
                "summary": _extract_first_paragraph(content or ""),
                "tech_stack": _extract_list_items(content or "", r"tech|stack|language|framework"),
                "capabilities": _extract_list_items(content or "", r"capabilit|feature|what it does"),
                "hidden_capabilities": _extract_list_items(content or "", r"hidden|unused|dead code|abandoned"),
                "dead_code": _extract_list_items(content or "", r"dead code|unused|orphan"),
                "insights": _extract_list_items(content or "", r"insight|surprising|notable|finding"),
                "connections": _extract_connections(content or "", name),
            }
            projects.append(project)

    # If no per-project blocks found, extract from section-based format
    if not projects:
        # Build a single "project" entry from the whole analysis
        # Extract insights from relevant sections
        insights = (
            _extract_list_items(text, r"surprising|finding|notable")
            or _extract_list_items(text, r"strength")
            or _extract_list_items(text, r"recommendation")
        )
        hidden = _extract_list_items(text, r"hidden|dead.code|unused|weakness")
        capabilities = _extract_list_items(text, r"capabilit|feature|what.works|strength")

        # Try to extract project name from title
        title_match = re.search(r"^#\s+(.+?)(?:\s*[—\-]|$)", text, re.MULTILINE)
        name = "analysis"
        if title_match:
            name = re.sub(r"[^a-z0-9_-]", "-", title_match.group(1).lower().strip()).strip("-")[:40]

        if insights or hidden or capabilities:
            projects.append({
                "name": name,
                "summary": _extract_first_paragraph(text),
                "tech_stack": _extract_list_items(text, r"tech|stack|architecture"),
                "capabilities": capabilities,
                "hidden_capabilities": hidden,
                "dead_code": _extract_list_items(text, r"dead.code"),
                "insights": insights,
                "connections": [],
            })

    # Extract cross-project connections from anywhere
    connections = _extract_cross_connections(text)

    # Extract dream proposals
    proposals = _extract_proposals(text)

    return {
        "projects": projects,
        "cross_project_connections": connections,
        "dream_proposals": proposals,
    }


def parse_workspace_files(workspace_dir: Path) -> dict:
    """Parse all markdown files in a dream workspace into structured data."""
    combined_text = ""

    # Read in priority order
    for filename in ["analysis.md", "notes.md", "DREAM_CONNECTIONS.md",
                     "DEEP_CODE_ANALYSIS.md", "SOURCE_CODE_INVENTORY.md"]:
        path = workspace_dir / filename
        if path.is_file():
            combined_text += f"\n\n# === {filename} ===\n\n"
            combined_text += path.read_text(errors="replace")

    # Also read any cluster-*.md files
    for path in sorted(workspace_dir.glob("cluster-*.md")):
        combined_text += f"\n\n# === {path.name} ===\n\n"
        combined_text += path.read_text(errors="replace")

    if not combined_text.strip():
        return {"projects": [], "cross_project_connections": [], "dream_proposals": []}

    return parse_dream_markdown(combined_text)


def _extract_first_paragraph(text: str) -> str:
    """Extract first non-heading paragraph."""
    if not text:
        return ""
    for line in text.split("\n"):
        line = line.strip()
        if line and not line.startswith("#") and not line.startswith("---") and not line.startswith("|"):
            return line[:500]
    return ""


def _extract_list_items(text: str, heading_pattern: str, max_items: int = 10) -> list[str]:
    """Extract bullet items under a heading matching pattern."""
    if not text:
        return []
    pattern = rf"^###?\s+.*?{heading_pattern}.*?$\n((?:[ \t]*[-*].*\n?)*)"
    match = re.search(pattern, text, re.MULTILINE | re.IGNORECASE)
    if not match:
        return []

    items = []
    for line in match.group(1).split("\n"):
        line = line.strip()
        if line.startswith("- ") or line.startswith("* "):
            item = line[2:].strip()
            if item and len(item) > 5:
                items.append(item[:200])
                if len(items) >= max_items:
                    break
    return items


def _extract_connections(text: str, source_name: str) -> list[dict]:
    """Extract connection mentions from a project block."""
    if not text:
        return []
    connections = []
    # Look for patterns like "connects to X", "integrates with X", "could feed X"
    patterns = [
        r"(?:connect|integrat|feed|bridge|link|combine|share)s?\s+(?:to|with)\s+[`\"]?(\w[\w-]+)[`\"]?",
        r"~/Desktop/(\w[\w-]+)/",
    ]
    targets_seen = set()
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            target = match.group(1).lower()
            if target != source_name and target not in targets_seen and len(target) > 2:
                targets_seen.add(target)
                # Get surrounding context
                start = max(0, match.start() - 50)
                end = min(len(text), match.end() + 100)
                context = text[start:end].replace("\n", " ").strip()
                connections.append({
                    "target": target,
                    "description": context[:200],
                    "strength": "medium",
                })
    return connections


def _extract_cross_connections(text: str) -> list[dict]:
    """Extract cross-project connections from full text."""
    connections = []
    # Look for "project-a → project-b" or "project-a <> project-b" patterns
    pattern = r"(\w[\w-]+)\s*(?:→|->|<>|↔|connects? to|integrates? with)\s*(\w[\w-]+)"
    seen = set()
    for match in re.finditer(pattern, text, re.IGNORECASE):
        source = match.group(1).lower()
        target = match.group(2).lower()
        key = f"{source}<>{target}"
        if key not in seen and source != target:
            seen.add(key)
            start = max(0, match.start() - 80)
            end = min(len(text), match.end() + 150)
            context = text[start:end].replace("\n", " ").strip()
            connections.append({
                "source": source,
                "target": target,
                "description": context[:300],
            })
    return connections


def _extract_proposals(text: str) -> list[dict]:
    """Extract dream project proposals from text."""
    proposals = []
    # Look for "Dream Project" or "Proposal" sections
    pattern = r"^###?\s+(?:Dream|Proposal|Idea)\s*(?:#?\d*)?[:\s]+(.+?)$\n(.*?)(?=^###?\s|\Z)"
    for match in re.finditer(pattern, text, re.MULTILINE | re.DOTALL | re.IGNORECASE):
        name = match.group(1).strip()
        body = match.group(2).strip()
        proposals.append({
            "name": name[:100],
            "description": body[:500],
            "combines": [],
            "architecture": "",
        })
    return proposals
