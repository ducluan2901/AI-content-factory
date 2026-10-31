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

The week-1 skeleton is built on these defaults: the Anthropic API for the script, title cards, a free Edge voice, and export to disk. Weeks 2 to 4 still wait on these answers.

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

Put an Anthropic API key in `.env` as `ANTHROPIC_API_KEY`. The script stage calls
`claude-sonnet-5-5` through the API, which costs about $0.025 per video. Set a spend cap on the
key in the Anthropic Console. `/health` reports whether a key is configured, never its value.

Run the API and the operator console in two terminals:

```bash
.\scripts\run_api.ps1
```

```bash
.\scripts\run_ui.ps1
```

API docs at <http://127.0.0.1:8000/docs>, operator console at <http://127.0.0.1:8501>. Both
bind to localhost only, because the MVP has no authentication.

To try the pipeline without an API key, set `LLM_PROVIDER=fixture` in `.env`. It replays a
recorded Vietnamese script from `tests/fixtures/llm/` and still uses the real voice and render.

Load the sample channels, trends and ideas (all flagged as samples; the scores are illustrative):

```bash
.\.venv\Scripts\python.exe scripts\seed_demo.py
```

Run the tests (offline, about 15 seconds, needs FFmpeg):

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
| Operator console (Video Studio) | `ui/Home.py` |
| Trend Radar and Content Planner pages | `ui/pages/` |
| Formats, TrendScore, idea → job | `app/planner.py` |
| Alembic migrations | `migrations/`, run by `app.db.migrate()` at API start |

Measured on this machine, 2026-10-31, demo topic, fixture script, real Vietnamese voice:

| Measure | Result |
|---|---|
| Video | 1080×1920, H.264/AAC, 30 fps, 53.9 s, 1.3 MB |
| Audio/video drift | 0.00 s |
| Render time | 4.5 s |
| Voice stage | 20 s to 2 min, dominated by edge-tts retries |
| Marginal cost | $0 |

Not built yet, per the backlog: research, separate strategy/script/SEO agents, stock footage,
loudness normalisation, and Alembic migrations. The `claude_cli` provider remains for trying
prompts during development only, and refuses to start with `APP_ENV=prod`.

## Sprint 1 of the 30-day plan: Trend Radar and Content Planner

Three formats drive everything: `comedy` (20–35 s), `facts` (30–45 s) and `gadget` (25–40 s). A job gets
the format's default length unless you set one; the quality gate judges the rendered length against that
target ±20 %.

| Endpoint | Purpose |
|---|---|
| `GET /formats` | the three formats and their windows |
| `GET, POST /channels` | channels: market, language, positioning, handles |
| `GET, POST /trends`, `GET, PATCH /trends/{id}` | trends with the five score components; the response includes the per-component breakdown |
| `GET, POST /ideas`, `PATCH /ideas/{id}` | the idea backlog, filterable by status, channel and format |
| `POST /ideas/{id}/jobs` | turn an idea into a video job; the job keeps `idea_id` and `channel_id` |

TrendScore is `0.30 velocity + 0.25 retention + 0.20 channel fit + 0.15 monetization + 0.10 feasibility`,
each 0–100. A component you leave empty counts as 50 and lowers `confidence`, and the trend is marked
`is_estimated`. Nothing scores a trend automatically: there is no scraping and no LLM call in this sprint.

## Sprint 2: script gate and format prompts

Every job now stops at `SCRIPT_REVIEW` after the script is written, before any voice or render time is
spent (plan §7.5, `SCRIPT_GENERATED → SCRIPT_APPROVED`). Pass `"review_script": false` to `POST /jobs` to skip
the stop.

| Endpoint | Purpose |
|---|---|
| `PUT /jobs/{id}/script` | save a human edit as a new script version; later stages are superseded |
| `POST /jobs/{id}/script/approve` | approve the script; the job continues from the voice stage |
| `POST /jobs/{id}/script/regenerate` | reject with notes; the next draft sees the notes and the previous draft |
| `POST /ideas/batch-jobs` | queue up to 20 ideas at once; each stops at script review |

The script prompt is `app/prompts/script.md` plus one of `format_comedy.md`, `format_facts.md`,
`format_gadget.md`. Each run records `prompt_version` as `name@sha8` for both files, so a change in output
can be traced to the prompt that produced it. The script review screen shows advisory checks: estimated
length against the target, scene count, hook length, card text length, platform limits, missing product
facts for gadgets, and any figure in the narration that needs verifying.

## Sprint 3: stock photos, Ken Burns motion, loudness

Each scene can now get a real Pexels stock photo instead of a plain gradient card. The script stage writes
a short English `visual_query` per scene (e.g. "person typing laptop office night"); VISUALS searches Pexels
with it, crops the photo to 9:16, darkens the bottom for legibility and composites the same caption a card
would carry. No match, no key configured, or a Pexels error all fall back to a title card - a scene is
never shipped with an unrecorded licence (BACKLOG R4). Every asset records its licence; stock photos carry
`licence: pexels`, `source_url`, `source_id` and `author`, same as a card's `licence: generated`.

Set `PEXELS_API_KEY` in `.env` to turn this on (a free account at pexels.com/api - create it yourself, this
app never creates accounts). With no key, `STOCK_PROVIDER=pexels` quietly behaves like `STOCK_PROVIDER=none`
and every scene is a card, exactly like before this sprint.

Every still, card or photo, gets a slow Ken Burns zoom (alternating in/out by scene index, up to 25%) before
the scenes are concatenated, instead of a static frame. The voice-over is loudness-normalised toward -14
LUFS after synthesis (single-pass `loudnorm`, an estimate, not an exact two-pass measurement - see the
`shortcut:` note in `app/media.py`); the quality gate's new `loudness` check is advisory and never blocks
approval, since the measured figure is only as good as that single pass.

Not done this sprint: hardcoded subtitle burn-in (`render_output.subtitles_burned` stays `False`); the
`SubtitleBuilder`'s SRT/VTT files are still the only subtitle output.

## Sprint 4: subtitle burn-in, risky-claim check, Video Studio

`POST /jobs` takes `"burn_subtitles": true` to hard-code the captions into the picture (SRT and VTT are
exported either way). The captions use the configured font, sit above the platform UI, and carry Vietnamese
diacritics. Burn-in copies the SRT and the font next to the render and refers to them by relative name,
because libass mishandles Windows drive-letter paths.

`app/safety_terms.txt` is a starter list of risky-claim phrases ("cam kết 100%", "kiếm tiền nhanh",
"guaranteed results", ...). A match is a warning at script review and in the quality gate, never a block:
a person decides. Edit the file freely.

The Home page doubles as the Video Studio: before you approve it shows every quality check with a
BLOCK / review / ok marker, a thumbnail per scene with its licence and attribution, and the subtitle file.

## Sprint 5: manual post tracking and analytics

Publishing stays manual: there is no code path to any platform. You upload by hand, then record the post
(`POST /jobs/{id}/publications`, only for an approved and exported video) and copy the platform's numbers in at
24 h, 72 h and 7 d (`PUT /publications/{id}/metrics/{milestone}`; sending it again corrects the entry).

| Endpoint | Purpose |
|---|---|
| `GET /publications` | recorded posts with their metrics and a classification per milestone |
| `GET /analytics/due` | milestones whose time has come and that still have no numbers |
| `GET /analytics/formats?milestone=7d` | per format: videos, median views, completion, engagement, label counts |

Classification follows the 30-day plan §4.3. A video is a **winner** when its views beat the median of the last
10 videos on the same channel and platform and completion is >= 25 % or engagement (likes + comments + shares
over views) is >= 3 %. It is a **loser** when neither target is met, **promising** when retention is on target
but views are not above the median, otherwise **average**. With fewer than 3 comparable videos nothing is called
a winner, and the reason says so. "Commercial winner" needs click and order data, so it waits for the
affiliate module. The Analytics page in the console shows all of this, plus which format has a signal.

## Security and content rules

- Secrets only in environment variables; `.env` is git-ignored and never committed.
- **No content is ever published automatically.** The MVP exports an asset bundle to disk; a
  human uploads it. There is no code path to a platform API — see ARCHITECTURE.md §8.
- Every visual asset carries a licence record, and the quality gate blocks a job whose assets
  have an unknown licence — see DATABASE.md §4.8.
