# AI Content Factory

Turns a topic or a product description into a ready-to-publish vertical short-form video
(TikTok / YouTube Shorts / Facebook Reels), with a mandatory human approval step before
anything is exported.

**Status: planning complete, no application code written yet.** This repository currently
contains only the design documents below. Implementation starts after founder approval.

## Documents

| Document | What it answers |
|---|---|
| [PRD.md](PRD.md) | What we are building, for whom, what "done" means, what is out of scope, and the honest position on the API-cost constraint |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Layers, the uniform stage contract, provider abstraction, why not LangGraph yet, Windows specifics, deliberate trade-offs |
| [DATABASE.md](DATABASE.md) | Full schema, the job-ID traceability spine, and the approval gate enforced at the storage layer |
| [BACKLOG.md](BACKLOG.md) | The minimum viable workflow, a 4-week backlog with gates, 12 technical risks, and all external dependencies |

**Read in this order:** BACKLOG.md Part A (the minimum viable workflow) → PRD.md §6 and §8 →
BACKLOG.md Part C (risks). Those three sections are where the decisions that need your
approval actually live.

## Decisions waiting on the founder

1. The cost / terms position — PRD.md §8.
2. The five open questions — PRD.md §10.
3. The minimum viable workflow and the 4-week plan — BACKLOG.md Part E.

Nothing is implemented until these are settled.

---

## Environment prerequisites (checked on this machine, 2026-10-31)

| Requirement | Status here | Action |
|---|---|---|
| Python 3.12+ | **Not installed.** `python` resolves to the Microsoft Store stub in `WindowsApps` and exits with code 9009 | install from python.org (see below) |
| FFmpeg + FFprobe | **Not installed** | install via winget or a static build |
| Git | installed (`C:\Program Files\Git`) | — |
| Node.js | installed | not required by this project |
| Claude CLI | installed | used by the `dev` LLM provider |

### Install Python 3.12

Download from <https://www.python.org/downloads/> (the python.org installer, **not** the
Microsoft Store version — the Store stub is what is currently on this machine and it breaks
virtual environments and the `py` launcher). During installation tick **"Add python.exe to
PATH"** and **"py launcher"**.

Or with winget:

```bash
winget install --id Python.Python.3.12 --source winget
```

### Install FFmpeg

```bash
winget install --id Gyan.FFmpeg --source winget
```

Then open a new terminal and verify both:

```bash
python --version && ffmpeg -version
```

`python --version` must print `Python 3.12.x` or later. If it prints nothing, the Store stub
is still ahead of the real Python in `PATH` — remove `%LOCALAPPDATA%\Microsoft\WindowsApps`
from `PATH`, or disable the stub under Settings → Apps → App execution aliases.

---

## Setup (once implementation starts — week 1, item 1.1)

These steps are the target of the first work item; the scripts do not exist yet.

```bash
python -m venv .venv
```

```bash
.\.venv\Scripts\Activate.ps1
```

```bash
pip install -e ".[dev]"
```

```bash
copy .env.example .env
```

Then edit `.env`. No API key is required for the default `dev` profile:

```
APP_ENV=dev
LLM_PROVIDER=claude_cli
TTS_PROVIDER=edge_tts
IMAGE_PROVIDER=title_card
FFMPEG_PATH=C:\path\to\ffmpeg.exe
DATABASE_URL=sqlite:///./storage/factory.db
```

Create the database schema:

```bash
alembic upgrade head
```

Run the API and the operator console in two terminals:

```bash
.\scripts\run_api.ps1
```

```bash
.\scripts\run_ui.ps1
```

API docs at <http://127.0.0.1:8000/docs>, operator console at <http://127.0.0.1:8501>.

## Security and content rules

- Secrets only in environment variables; `.env` is git-ignored and never committed.
- **No content is ever published automatically.** The MVP exports an asset bundle to disk; a
  human uploads it. There is no code path to a platform API — see ARCHITECTURE.md §8.
- Every visual asset carries a licence record, and the quality gate blocks a job whose assets
  have an unknown licence — see DATABASE.md §4.8.
