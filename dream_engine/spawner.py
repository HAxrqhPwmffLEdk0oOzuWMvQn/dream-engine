"""Claude Code subprocess wrapper for deep dream analysis.

Spawns Claude Code (Opus) in a temp workspace with structured output requirements.
Captures stdout, extracts JSON blocks, falls back to raw markdown parsing.
"""

import asyncio
import logging
import shutil
import tempfile
import time
from pathlib import Path

from .config import settings
from .models import ProjectInfo

logger = logging.getLogger(__name__)

WORKSPACE_CLAUDE_MD = """# Spark Dream Workspace

## READ-ONLY RULES
- Read any file on the system (~/Desktop/*, /tmp/*, etc.)
- Write files ONLY in your current working directory
- Do NOT modify any file outside this workspace
- Do NOT run destructive commands (rm, mv, git push, docker exec, systemctl restart, etc.)

## OUTPUT FORMAT
Write your analysis to analysis.md in this directory.

At the END of your analysis, include a JSON block between these exact delimiters:

---JSON_OUTPUT_START---
{
  "projects": [
    {
      "name": "project-name",
      "summary": "One paragraph summary from reading source code",
      "tech_stack": ["python", "fastapi"],
      "capabilities": ["capability 1", "capability 2"],
      "hidden_capabilities": ["unused function at file.py:123"],
      "dead_code": ["abandoned feature in old_module.py"],
      "insights": ["Most surprising finding about this project"],
      "connections": [
        {"target": "other-project", "description": "How they could connect", "strength": "high"}
      ]
    }
  ],
  "cross_project_connections": [
    {
      "source": "project-a",
      "target": "project-b",
      "description": "Specific connection proposal with file paths",
      "source_file": "src/api.py:45",
      "target_file": "src/handler.ts:120"
    }
  ],
  "dream_proposals": [
    {
      "name": "Proposed project name",
      "description": "What it would do",
      "combines": ["project-a", "project-b"],
      "architecture": "Brief architecture sketch"
    }
  ]
}
---JSON_OUTPUT_END---

You are a reader and dreamer. Observe, analyze, connect, synthesize. Do not touch.
"""

JSON_START_DELIM = "---JSON_OUTPUT_START---"
JSON_END_DELIM = "---JSON_OUTPUT_END---"


def _find_claude_binary() -> str:
    """Find the Claude Code binary."""
    # Check common locations
    candidates = [
        settings.claude_binary,
        shutil.which("claude"),
        str(Path.home() / ".local" / "bin" / "claude"),
    ]
    for c in candidates:
        if c and Path(c).is_file():
            return str(c)
    # Default to just "claude" and hope PATH has it
    return "claude"


async def spawn_claude(
    task_prompt: str,
    workspace_dir: Path | None = None,
    model: str | None = None,
    max_turns: int | None = None,
    timeout_sec: int | None = None,
) -> dict:
    """Spawn Claude Code in --print mode and capture output.

    Returns:
        {
            "workspace": str,       # path to workspace dir
            "stdout": str,          # raw stdout
            "analysis_md": str,     # contents of analysis.md if written
            "json_output": dict,    # parsed JSON block if found
            "exit_code": int,
            "duration_sec": float,
            "timed_out": bool,
        }
    """
    model = model or settings.claude_model
    max_turns = max_turns or settings.claude_max_turns
    timeout_sec = timeout_sec or settings.cluster_timeout_sec

    # Create workspace
    if workspace_dir is None:
        workspace_dir = Path(tempfile.mkdtemp(
            prefix="dream-",
            dir=settings.dreams_dir,
        ))
    workspace_dir.mkdir(parents=True, exist_ok=True)

    # Seed CLAUDE.md
    (workspace_dir / "CLAUDE.md").write_text(WORKSPACE_CLAUDE_MD)

    claude_bin = _find_claude_binary()
    full_prompt = f"Read CLAUDE.md first. {task_prompt}"

    cmd = [
        claude_bin,
        "--print",
        "--dangerously-skip-permissions",
        "--max-turns", str(max_turns),
        "--model", model,
        full_prompt,
    ]

    logger.info(f"Spawning Claude Code ({model}, max_turns={max_turns}, timeout={timeout_sec}s)")
    logger.info(f"Workspace: {workspace_dir}")

    start = time.time()
    timed_out = False

    # Live log file for real-time monitoring
    log_file = workspace_dir / "dream.log"

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(workspace_dir),
        )

        # Stream stdout to log file in real-time
        stdout_chunks = []
        try:
            async def stream_output():
                with open(log_file, "w") as f:
                    while True:
                        chunk = await proc.stdout.read(4096)
                        if not chunk:
                            break
                        decoded = chunk.decode("utf-8", errors="replace")
                        stdout_chunks.append(decoded)
                        f.write(decoded)
                        f.flush()
                        # Also monitor analysis.md growth
                        analysis_path = workspace_dir / "analysis.md"
                        if analysis_path.is_file():
                            size = analysis_path.stat().st_size
                            if size > 0:
                                f.write(f"\n[analysis.md: {size} bytes]\n")
                                f.flush()

            await asyncio.wait_for(stream_output(), timeout=timeout_sec)
            await proc.wait()
        except asyncio.TimeoutError:
            logger.warning(f"Claude Code timed out after {timeout_sec}s — killing")
            proc.kill()
            await proc.wait()
            timed_out = True

    except FileNotFoundError:
        logger.error(f"Claude Code binary not found: {claude_bin}")
        return {
            "workspace": str(workspace_dir),
            "stdout": "",
            "analysis_md": "",
            "json_output": None,
            "exit_code": -1,
            "duration_sec": time.time() - start,
            "timed_out": False,
            "error": f"Binary not found: {claude_bin}",
        }

    duration = time.time() - start
    stdout = "".join(stdout_chunks)
    exit_code = proc.returncode if proc.returncode is not None else -1

    # Read analysis.md if Claude wrote it
    analysis_md = ""
    analysis_path = workspace_dir / "analysis.md"
    if analysis_path.is_file():
        analysis_md = analysis_path.read_text(errors="replace")

    # Extract JSON block from stdout or analysis.md
    json_output = _extract_json_block(stdout) or _extract_json_block(analysis_md)

    logger.info(
        f"Claude Code finished: exit={exit_code}, duration={duration:.0f}s, "
        f"timed_out={timed_out}, json={'yes' if json_output else 'no'}, "
        f"analysis_md={len(analysis_md)} chars"
    )

    return {
        "workspace": str(workspace_dir),
        "stdout": stdout,
        "analysis_md": analysis_md,
        "json_output": json_output,
        "exit_code": exit_code,
        "duration_sec": round(duration, 1),
        "timed_out": timed_out,
    }


def _extract_json_block(text: str) -> dict | None:
    """Extract JSON between delimiters from text."""
    if not text or JSON_START_DELIM not in text:
        return None

    try:
        start_idx = text.index(JSON_START_DELIM) + len(JSON_START_DELIM)
        end_idx = text.index(JSON_END_DELIM, start_idx)
        json_str = text[start_idx:end_idx].strip()

        import json
        return json.loads(json_str)
    except (ValueError, json.JSONDecodeError) as e:
        logger.warning(f"Failed to parse JSON block: {e}")
        return None
