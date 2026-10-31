# AI Content Factory

Turns a topic or a product description into a ready-to-publish vertical short-form video
(TikTok / YouTube Shorts / Facebook Reels), with a mandatory human approval step before
anything is exported.

**Status: week-1 walking skeleton built and tested.** A topic becomes an approved, exported
1080×1920 Vietnamese video on Windows. See "What is built" below.

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

The week-1 skeleton is built on the defaults these documents recommend: `dev` providers, title cards, a free Edge voice, and export to disk. Weeks 2 to 4 still wait on these answers.

---

## Setup (Windows PowerShell 5.1 or later)

Prerequisites: Python 3.12+ from python.org (the `py` launcher is enough) or `uv`. The setup
script installs FFmpeg with winget if it is missing.

```bash
.\scripts\setup_windows.ps1
```

It creates `.venv`, installs the dependencies, finds FFmpeg, and writes `.env` from
`.env.example` with the absolute FFmpeg paths. Windows PowerShell 5.1 has no `&&`; use `;` or
separate lines.

Log the Claude CLI in once, in your own terminal, so the `dev` LLM provider can call it:

```bash
claude -p "hello"
```

Run the API and the operator console in two terminals:

```bash
.\scripts\run_api.ps1
```

```bash
.\scripts\run_ui.ps1
```

API docs at <http://127.0.0.1:8000/docs>, operator console at <http://127.0.0.1:8501>. Both
bind to localhost only, because the MVP has no authentication.

To try the pipeline without the Claude CLI, set `LLM_PROVIDER=fixture` in `.env`. It replays a
recorded Vietnamese script from `tests/fixtures/llm/` and still uses the real voice and render.

Run the tests (offline, about 10 seconds, needs FFmpeg):

```bash
.\.venv\Scripts\python.exe -m pytest -q
```

## What is built (week-1 walking skeleton, BACKLOG Part A)

`POST /jobs` runs SCRIPT, VOICE, VISUALS, SUBTITLES, RENDER and QUALITY, then parks the job at
`AWAITING_APPROVAL`. `POST /jobs/{id}/approve` records the approval and writes the export
bundle to `storage/jobs/{id}/export/` plus a zip. `POST /jobs/{id}/reject` and
`POST /jobs/{id}/run` with `from_stage` re-run part of the pipeline and keep the old runs in the
audit trail.

| Area | Where |
|---|---|
| Config and the prod boot guard | `app/config.py` |
| Job, stage run and approval tables, approval CHECK constraint | `app/db.py` |
| Claude CLI, edge-tts and fixture providers | `app/providers.py` |
| Title cards, FFmpeg render, SRT/VTT | `app/media.py` |
| Stages, runner, orchestrator, approval gate, export | `app/pipeline.py` |
| HTTP API and in-process worker | `app/main.py` |
| Operator console | `ui/Home.py` |

Measured on this machine, 2026-10-31, demo topic, fixture script, real Vietnamese voice:

| Measure | Result |
|---|---|
| Video | 1080×1920, H.264/AAC, 30 fps, 53.9 s, 1.3 MB |
| Audio/video drift | 0.00 s |
| Render time | 4.5 s |
| Voice stage | 20 s to 2 min, dominated by edge-tts retries |
| Marginal cost | $0 |

Not built yet, per the backlog: research, separate strategy/script/SEO agents, stock footage,
loudness normalisation, Alembic migrations, and the Anthropic API provider for `prod`.

## Security and content rules

- Secrets only in environment variables; `.env` is git-ignored and never committed.
- **No content is ever published automatically.** The MVP exports an asset bundle to disk; a
  human uploads it. There is no code path to a platform API — see ARCHITECTURE.md §8.
- Every visual asset carries a licence record, and the quality gate blocks a job whose assets
  have an unknown licence — see DATABASE.md §4.8.
