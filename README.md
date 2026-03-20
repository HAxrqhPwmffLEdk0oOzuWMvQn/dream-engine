# Dream Engine

An AI-powered project analysis engine. Scans your codebase, reads every source file, finds hidden connections between projects, and generates a static website with insights — like having a researcher who reads all your code overnight and reports back in the morning.

## How It Works

1. **Scan** — discovers project directories and reads `memory-bank/` files (structured project docs)
2. **Ingest** — chunks project knowledge into searchable memory
3. **Dream** — spawns Claude Code (Opus) to deeply read source code, finding hidden capabilities, dead code, and cross-project connections
4. **Generate** — builds a static website with findings: project grid, connections map, dream proposals, buried treasure

### Dream Modes

- **Full Dream** — analyzes all projects in clusters (batches of 10), then runs a grand synthesis pass. Generates architecture maps, cross-project connections, unrealized potentials, and new project proposals. Takes 30–120 minutes depending on project count.
- **Nightly Dream** — picks the most stale/changed projects and runs focused analysis on each. Designed for cron or manual nightly runs. Typically 10–30 minutes.

### Generated Site

The dream site is a static HTML dashboard with 5 pages plus per-dream detail reports:

| Page | Content |
|------|---------|
| **Home** | Stats, latest dream executive summary, surprising discoveries, top insights |
| **Projects** | Card grid of all projects with tech stack, capabilities, last ingested date |
| **Connections** | Table of cross-project connections with source files and dream date |
| **Dreams** | Per-run summaries with proposals, links to full detail pages |
| **Gaps** | Hidden capabilities, dead code worth reviving, unanalyzed projects |

Each dream run also gets a **detail page** with:
- Executive summary
- ASCII architecture diagram
- Top unrealized potentials (with impact ratings)
- Surprising code discoveries (with file:line locations)
- New project proposals (with architecture sketches)
- Shared infrastructure patterns
- Prioritized recommendations (immediate / short-term / long-term)
- Cluster analysis breakdowns

## Quick Start

```bash
git clone https://github.com/youruser/dream-engine.git
cd dream-engine
uv sync
uv run dream
```

First run walks you through setup — picks which folders to scan, configures the Claude model, and sets up optional integrations.

## What It Scans For

In each project directory, the engine looks for:

- `memory-bank/activeContext.md` — current project state
- `memory-bank/progress.md` — what's done, what's next
- `memory-bank/techContext.md` — architecture, stack
- `memory-bank/productContext.md` — why it exists
- `CLAUDE.md` — quick-reference setup docs
- `STATUS.md` — project status

During deep dreams, Claude Code reads actual source files (`.py`, `.ts`, `.js`, `.go`) and finds things documentation doesn't capture.

## Dashboard

Once running, visit `http://localhost:8160`:

- **Dream Site** (`/`) — the generated analysis website
- **Admin** (`/admin`) — project list, ingest controls, dream triggers, live dream log

## API

```
POST /api/ingest              — scan and ingest changed projects
POST /api/ingest/{name}       — ingest single project
POST /api/dream/start         — full dream (all clusters, Opus)
POST /api/dream/nightly       — dream about stale projects only
GET  /api/dream/status        — current dream progress
GET  /api/dream/history       — past dream runs
GET  /api/dream/log           — tail of active dream output
GET  /api/dream/log/stream    — SSE stream for live monitoring
GET  /api/projects            — all discovered projects
GET  /api/projects/{name}     — project knowledge detail
POST /api/site/regenerate     — rebuild dream site from current data
```

## Architecture

```
dream_engine/
  main.py       — FastAPI app, API routes, admin dashboard
  config.py     — Settings from .env (pydantic-settings)
  ingestor.py   — Project discovery, memory-bank parsing, chunking
  dreamer.py    — Dream orchestration (full + nightly modes)
  spawner.py    — Claude Code subprocess wrapper with JSON extraction
  parser.py     — Markdown → structured data parser + narrative extraction
  sitegen.py    — Static site generator (HTML pages from dream outputs)
  models.py     — Pydantic models (ProjectInfo, DreamStatus, etc.)
  setup.py      — First-run interactive setup
  cli.py        — CLI entry point

site/           — Generated static HTML (served at /)
site-template/  — CSS template (copied during regeneration)
dreams/         — Dream output directory (analysis.md per project per run)
state/          — Ingest state, dream history, cluster config
prompts/        — (reserved for custom dream prompts)
```

## Optional Integrations

### Wiki.js

Push project pages to a Wiki.js instance. Each ingest updates the corresponding wiki page.

```env
DREAM_WIKI_ENABLED=true
DREAM_WIKI_UPDATE_SCRIPT=~/bin/wiki-update.sh
DREAM_WIKI_EXPORT_SCRIPT=~/bin/wiki-export-for-claude.sh
```

### ElizaOS / Milady

Inject project knowledge into an ElizaOS agent's memory system, making the agent a subject matter expert on your projects.

```env
DREAM_MILADY_ENABLED=true
DREAM_MILADY_API_URL=http://localhost:2138
```

## Configuration

All config lives in `.env` (generated by first-run setup):

```env
DREAM_SCAN_PATHS=~/Desktop,~/Projects   # comma-separated paths to scan
DREAM_CLAUDE_MODEL=opus                  # opus, sonnet, or haiku
DREAM_NIGHTLY_BATCH_SIZE=15              # projects per nightly dream
DREAM_WIKI_ENABLED=false                 # optional Wiki.js integration
DREAM_MILADY_ENABLED=false               # optional ElizaOS integration
```

## Memory Banks

The engine works best with projects that have `memory-bank/` directories — structured markdown files that describe each project's state. These follow the [Cline memory bank pattern](https://docs.cline.bot/improving-your-workflow/memory-bank):

```
my-project/
  memory-bank/
    activeContext.md    # current state, what's running
    progress.md         # what works, what's next
    techContext.md       # architecture, tech stack
    productContext.md    # why it exists
    projectbrief.md     # one-paragraph summary
```

Projects without memory-banks are still discovered and can be deeply analyzed by the dream engine — they just won't have pre-existing documentation to ingest.

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (package manager)
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) (for deep dreams)
- Anthropic API key (set in your environment)

## License

MIT
