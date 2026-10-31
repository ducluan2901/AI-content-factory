# BACKLOG, RISKS & MINIMUM VIABLE WORKFLOW

**Version:** 0.1 (draft for founder approval)
**Date:** 2026-10-31
**Assumed capacity:** one engineer (Claude Code + founder review), ~5 productive days/week.

---

## Part A — The minimum viable workflow

Before the 4-week plan, the single most important question: **what is the smallest thing that
produces one complete publishable video?** Everything else is an improvement on this spine.

### A.1 The spine (the "walking skeleton")

```
topic → [LLM: brief+script in ONE call] → [edge-tts: Vietnamese voice-over]
      → [timings from TTS] → [Pillow: title cards from scene text]
      → [FFmpeg: concat cards + audio, 1080×1920] → [SRT from timings]
      → [operator approves] → folder on disk
```

Seven steps, one LLM call, zero paid APIs, no stock footage, no research. A video made this
way is *publishable but plain*: text cards over a gradient with a voice-over — the format
thousands of faceless AI/tech channels already use successfully on TikTok.

**Build this in week 1, end to end, before building any agent properly.** Reasons:

1. It proves the hardest and least-negotiable part first: FFmpeg on Windows, Vietnamese TTS
   quality, Vietnamese subtitle timing, and vertical render correctness. If Vietnamese TTS is
   not good enough, that is a week-1 discovery, not a week-3 one.
2. Every later stage (research, strategy, SEO, stock visuals, quality) is then an *insertion*
   into a working pipeline, each independently demo-able and independently abandonable.
3. It gives the founder a video to look at in week 1, which is the only honest way to
   validate whether the output is worth building a business on.

### A.2 What the spine deliberately omits, and when each gets added

| Omitted | Added in | Why it can wait |
|---|---|---|
| Research agent | W2 | The spine's LLM already knows enough about AI/tech for a generic script. Grounding matters for credibility, not for proving the pipeline. |
| Separate strategy + script agents | W2 | One combined call is worse but works; splitting improves control, not feasibility. |
| SEO agent | W2 | A human can write a caption in 2 minutes; the pipeline is the bottleneck, not the caption. |
| Stock footage | W3 | Title cards are a legitimate, licence-clean format. Stock is an upgrade with a licensing surface. |
| Quality gate | W3 | A human reviews every video in the MVP anyway. The gate scales review; it does not enable it. |
| Partial re-run | W3 | Re-running whole jobs is cheap at $0 marginal cost. |

### A.3 Definition of "one complete video" (the week-1 exit test)

A 45–60s, 1080×1920, H.264/AAC MP4 with a Vietnamese voice-over, a matching `vi.srt` whose
cues align to within 300 ms, a title and caption, and an export folder — produced by one API
call plus one approval click, on Windows, from a cold start.

---

## Part B — 4-week development backlog

Each week ends with a **demo** (something the founder can watch or click) and a **gate**
(a question that, answered badly, changes the plan). Work does not proceed past a failed gate.

### Week 0 — now: approve these documents
Review PRD.md §10 (five open questions), ARCHITECTURE.md §6 (LangGraph), PRD §8 (the
cost/terms position). **Nothing is built until this is signed off.**

---

### Week 1 (Nov 2 – Nov 8) — Foundations + the walking skeleton

| # | Item | Done when |
|---|---|---|
| 1.1 | Windows environment: Python 3.12, FFmpeg, `scripts/setup_windows.ps1`, `.env.example` | a fresh machine is ready from one script + README |
| 1.2 | Project skeleton, `pyproject.toml`, structlog, typed error hierarchy, `pytest` + `ruff` + `mypy` + import-linter in CI | `pytest` green on an empty suite; the layer rule is machine-checked |
| 1.3 | SQLAlchemy models for all DATABASE.md tables + Alembic initial revision | `alembic upgrade head` creates the schema; constraint tests prove the `EXPORTED`/`approval_id` check bites |
| 1.4 | Stage contract, `StageRunner` (timeout, retry, logging, `stage_run` persistence), `Orchestrator`, registry | a no-op stage runs, times out, retries and logs, all covered by tests |
| 1.5 | Provider ports + `FixtureProvider` for LLM/TTS/search/stock | the full pipeline is runnable offline with zero cost |
| 1.6 | `ClaudeCliProvider` (dev) and `AnthropicApiProvider` (prod), with the `dev_only` boot guard | both produce schema-valid JSON for one prompt; `APP_ENV=prod` + `claude_cli` refuses to boot |
| 1.7 | **Spine stage 1:** combined brief+script stage (one LLM call, Vietnamese, 45–60s, scene list) | a schema-valid script with scenes, from a topic |
| 1.8 | **Spine stage 2:** `EdgeTtsProvider` — Vietnamese voice-over + segment timings | an MP3 plus segment timings, duration within ±10 % of the estimate |
| 1.9 | **Spine stage 3:** `TitleCardRenderer` (Pillow) — one card per scene, Vietnamese-safe font | PNG cards with correct diacritics, readable at 1080×1920 |
| 1.10 | **Spine stage 4:** `FFmpegRenderer` — cards + audio → 1080×1920 H.264/AAC MP4 | `ffprobe` confirms the exact spec |
| 1.11 | **Spine stage 5:** `SubtitleBuilder` — SRT from segment timings, Vietnamese line rules | cues align within 300 ms; no mid-syllable breaks |
| 1.12 | `POST /jobs`, `GET /jobs/{id}`, `POST /jobs/{id}/approve`, export to a folder; minimal Streamlit page with a video player and an approve button | **DEMO: the A.3 exit test passes** |

**Gate 1:** Is the output watchable? Is the Vietnamese voice acceptable (PRD §10 Q3)? Is the
latency sane? → If the voice is not acceptable, week 2 changes to a paid-TTS spike before
anything else, because voice quality caps the whole product's ceiling.

---

### Week 2 (Nov 9 – Nov 15) — Real agents: research, strategy, script, SEO

| # | Item | Done when |
|---|---|---|
| 2.1 | `SearchProvider` (DuckDuckGo / free tier) + page fetch with a politeness limiter | 10 results with titles, URLs and snippets for a Vietnamese query |
| 2.2 | **ResearchAgent** — structured `research_item` rows, each with a source URL and a verified flag | ≥ 8 items, ≥ 6 verified, for the demo topic |
| 2.3 | **StrategyAgent** — `content_brief` (angle, hook, audience, pain, promise, CTA, tone, scene count) | a brief a human would not be embarrassed to brief a freelancer with |
| 2.4 | **ScriptAgent** — replaces the spine's combined stage; per-scene source links; Vietnamese syllable-based duration model | scenes cite `research_item` ids; estimate within ±15 % of the actual voice-over |
| 2.5 | **SeoAgent** — per-platform title/caption/hashtags with character limits | all three platforms' limits respected, checked by test |
| 2.6 | Prompt versioning (`prompt_version` table) + prompt/response audit files | re-running a job records which prompt version produced it |
| 2.7 | Stage-level concurrency (subtitles ∥ visuals), per-job cost and latency rollups | the UI shows cost and duration per stage |
| 2.8 | Streamlit stage inspector: input/output per stage, sources list, timeline | **DEMO: PRD S3 (traceability) demonstrated on a real job** |

**Gate 2:** Does grounded research measurably improve the script versus the week-1 combined
call? Judge two videos on the same topic side by side. → If not, research is demoted to
optional and that week's budget moves to visuals, which is where viewer retention actually
lives.

---

### Week 3 (Nov 16 – Nov 22) — Visuals, quality gate, re-run, approval UX

| # | Item | Done when |
|---|---|---|
| 3.1 | `StockMediaProvider` (Pexels free API): per-scene search from `visual_direction`, download, cache, **licence record written** | each scene has a clip or image with a complete `media_asset` licence row |
| 3.2 | Renderer upgrade: Ken Burns on stills, cuts on segment boundaries, text overlay, safe-area margins for platform UI, optional music slot | the demo video uses real footage, with no text under TikTok's UI chrome |
| 3.3 | `AudioAnalyzer`: loudness normalisation to ≈ −14 LUFS, silence trim | `ffprobe`/`loudnorm` confirms the target |
| 3.4 | **QualityGate** rules: `duration_window`, `aspect_ratio`, `audio_present`, `loudness`, `subtitle_readability`, `platform_limits`, `asset_licence`, `claim_grounding`, `safety_terms` | **PRD S2 passes**: a 20s script and an unlicensed asset are each caught with a clear message |
| 3.5 | Per-stage re-run API + UI, `superseded_by` chaining, human script editing | **PRD S5 passes**: reject the script, edit, re-run from stage 4, keep the research |
| 3.6 | Approval screen: video player, script, sources, warnings to acknowledge, approve/reject with notes; append-only `approval` rows | **PRD S4 provable** — a test asserts no code path exports without an approval row |
| 3.7 | Export bundle: MP4, SRT, thumbnail, captions `.txt`, `manifest.json` with full provenance, zip | **DEMO: two finished videos, one approved, one caught by the gate** |

**Gate 3:** Is a gate-passed video publishable without the operator touching it? Count the
manual fixes needed on five videos. → More than one fix per video on average means week 4
goes to closing that specific gap instead of to hardening.

---

### Week 4 (Nov 23 – Nov 29) — Reliability, throughput, documentation, handover

| # | Item | Done when |
|---|---|---|
| 4.1 | Resume-after-crash across all stages; kill the process mid-render and restart | the job continues from the last successful stage |
| 4.2 | Test coverage pass: unit tests per stage, integration tests per provider, one full offline e2e run on fixtures in CI | CI runs the whole pipeline with no network and no cost |
| 4.3 | Batch submission (a list of topics) + the in-process queue, sequential with a concurrency cap | 10 topics submitted at once reach awaiting-approval unattended |
| 4.4 | Operator queue UI: filters, cost column, latency column, bulk export | the founder can run a day's production from one screen |
| 4.5 | English-language path (PRD secondary market): language-parameterised prompts, voice and subtitle rules | one English video passes the same gate |
| 4.6 | Cost and latency report: per video, per stage, per provider; verify S6 and S7 against 10 real jobs | measured numbers, written into the README |
| 4.7 | `README.md` setup, `RUNBOOK.md` (what to do when a stage fails), `scripts/prune_storage.ps1` | a second person can install and operate it from the docs alone |
| 4.8 | **LangGraph decision point** (ARCHITECTURE.md §6): review the week-5 backlog and decide | a written decision, either way |
| 4.9 | Platform publishing spec (not implementation): per-platform API requirements, approval timelines, upload constraints | a document that makes week 5+ estimable |

**Gate 4 / MVP exit:** all seven PRD §6 success criteria pass, measured, on a clean machine.

---

### Explicitly not in the 4 weeks (week 5+ candidates)

Automatic publishing; multi-tenancy and auth; a React front end; AI image/video generation;
voice cloning; A/B script variants; analytics feedback; client portal; scheduling calendar;
Postgres migration.

---

## Part C — Technical risks

Ordered by expected cost (probability × impact). The first four are the ones that can
actually derail the plan.

| # | Risk | P | Impact | Mitigation | Trigger / fallback |
|---|---|---|---|---|---|
| R1 | **Claude Code Max as a product backend is a terms and operational risk** (PRD §8) | High | Could invalidate the cost model the business plan assumes | Provider abstraction from day one; `dev_only` boot guard; `AnthropicApiProvider` built in week 1, not later | Founder decision in week 0. If `prod` is required for client work, the cost model is ~$0.01–0.05/video — model it now, not after launch |
| R2 | **Vietnamese TTS quality is the product's ceiling.** A robotic voice makes a technically perfect video unpublishable | Med-High | Caps the whole product | `EdgeTtsProvider` (free, genuinely decent Vietnamese neural voices) tested in **week 1**, deliberately early | Gate 1. Fallback: paid Vietnamese TTS (~$0.01–0.05/video), or a human-recorded voice for hero content |
| R3 | **Vietnamese subtitle and timing correctness**: diacritics, no mid-syllable breaks, 2 lines × ~20 chars, drift against audio | Med | Visibly amateur output | Timings come from the TTS segment data, never from word-count estimates; one timing source for audio and subtitles; a font with full Vietnamese coverage is bundled, not assumed | Automated test asserting ≤ 300 ms drift. Fallback: forced alignment with a local Whisper model (adds a heavy dependency — avoid if possible) |
| R4 | **Stock-footage licensing**, especially for paying clients (PRD D3) | Low-Med | Legal and reputational cost far above the build cost | Licence recorded per asset; quality gate blocks `unknown`; only permissive free-tier APIs; no scraped media; attribution carried into the export manifest | If no clean clip is found for a scene, fall back to a generated title card rather than shipping an unknown licence |
| R5 | FFmpeg on Windows: path handling, Unicode in filter text, codec availability, long paths | Med | Blocks the render stage | Configured binary path probed at startup; short hashed filenames; subtitle text passed via file, never via a command-line filter string; render covered by `ffprobe` assertions | Week 1 item 1.10 is the early canary |
| R6 | LLM output drift and schema violations | Med | Pipeline stalls, inconsistent quality | JSON-schema'd requests, Pydantic validation, one error-fed repair attempt, then fail to a human; prompt versioning; fixture tests pin expected shapes | If the repair rate exceeds ~10 %, move that stage to a stronger model |
| R7 | Hallucinated facts shipped as confident claims in the AI/tech niche | Med | Channel credibility, which is the asset | Every claim links to a `research_item`; `claim_grounding` gate rule; unverified claims surfaced at approval | A human approves every video in the MVP — this risk is bounded by design, and that is exactly why the approval gate is non-negotiable |
| R8 | Research quality from free search: thin, stale or low-quality sources for Vietnamese AI queries | Med | Weak scripts | Query in both Vietnamese and English and merge; prefer recent sources; `published_at` recorded | Gate 2. Fallback: a curated RSS/source whitelist for the AI niche (cheap and surprisingly effective) |
| R9 | Platform policy on AI-generated content (TikTok/YouTube/Meta disclosure rules) | Med | Reach throttling or takedowns | Export manifest records that content is AI-generated so disclosure is easy; week 4.9 documents each platform's current rules | Research in week 4; this is a publishing-time concern, and publishing is manual in the MVP |
| R10 | Scope creep toward SaaS before the first video earns money | **High** | The classic way this project fails | PRD §5.2 out-of-scope list; weekly gates; ARCHITECTURE §9 caps future-proofing at one column plus one interface | Any new feature request gets measured against "does this get a video published today?" |
| R11 | SQLite / single-process limits | Low | Throughput ceiling | ARCHITECTURE §10 documents the break points | > 50 jobs/day or concurrent operators ⇒ Postgres + a real queue |
| R12 | Python 3.12 and FFmpeg are not installed on the target machine today | Certain | Blocks day one | `scripts/setup_windows.ps1` + README as week-1 item 1.1 | Already identified; see README.md |

## Part D — External dependencies

### D.1 Hard dependencies (the MVP cannot ship without these)

| Dependency | Purpose | Cost | Risk if it disappears |
|---|---|---|---|
| Python 3.12+ | runtime | free | none |
| FFmpeg / FFprobe (GPL build) | render, probe, loudness | free | none; vendored binary possible |
| SQLite | persistence | free (bundled) | none |
| FastAPI · Uvicorn · Pydantic v2 · SQLAlchemy 2 · Alembic · structlog · Streamlit · Pillow · httpx · pytest | application | free | none |
| **An LLM**: `claude` CLI (dev) **or** an Anthropic API key (prod) | all agent stages | subscription or ~$0.01–0.05/video | **R1** — mitigated by the provider abstraction |
| **A TTS engine**: `edge-tts` (free, unofficial Microsoft Edge endpoint) | voice-over | free | **Medium: an unofficial endpoint can break without notice.** A paid-TTS adapter is the documented fallback |
| A Vietnamese-complete font (Noto Sans / Be Vietnam Pro, SIL OFL) | title cards, burned subtitles | free | none; bundled with the repo |

### D.2 Soft dependencies (the MVP degrades gracefully without these)

| Dependency | Purpose | Cost | Degradation |
|---|---|---|---|
| Web search (DuckDuckGo free / a search API) | research | free / low | ungrounded scripts, flagged as unverified |
| Pexels / Pixabay API | stock footage | free, keyed | title-card-only videos (the week-1 format) |
| A music library | background track | free tier or owned | silent background; the voice-over carries it |

### D.3 Deferred dependencies (designed for, not integrated)

TikTok Content Posting API, YouTube Data API v3, Facebook Graph API — each needs app review
and business verification, measured in weeks. **This is the main reason publishing is out of
MVP scope**: it is an approvals problem, not an engineering one, and it must not sit on the
critical path to the first video.

### D.4 Secrets

All via environment variables, `.env` git-ignored, `.env.example` committed with empty values.
No key in code, in the database, in logs or in an export manifest. A pre-commit secret scan is
part of week-1 item 1.2.

---

## Part E — What the founder is being asked to approve

1. **The minimum viable workflow (Part A):** ship a plain, title-card video end to end in
   week 1, then upgrade it. Rather than building all ten agents first and discovering the
   render or the voice is wrong in week 3.
2. **The 4-week backlog and its four gates**, including the possibility that Gate 1 or Gate 2
   redirects a week's work.
3. **The cost position (PRD §8):** `dev` providers for our own work, `prod` API for client
   work.
4. **The five open questions in PRD §10.**

On approval, work starts at Week 1 item 1.1 and nothing is built ahead of the plan.

---

## Part F — Mapping to the 30-day plan (added 2026-10-31)

`AI_Viral_Media_Network_30_Day_Plan.md` supersedes the week plan above where they differ. The pipeline,
the approval wall and the risk list stay; the niche, the video length and the order of work change.

| Plan sprint | Days | Covers | Relation to Parts A–B |
|---|---|---|---|
| 1 | 1–3 | channels, Trend Radar, Content Planner, TrendScore, dashboards 1–2 | new; **done** on branch `sprint-1-trend-planner` |
| 2 | 4–7 | Script agent per format, prompt templates | **done** on branch `sprint-2-script-agent`: script gate, edit, regenerate with notes, per-format prompts with versions, batch jobs. Research for `facts` still open |
| 3 | 8–12 | TTS, image, video renderer | W1 spine exists; adds an image provider decision (PRD Q2) |
| 4 | 13–17 | QC, subtitles, Video Studio | W3 3.3–3.6 |
| 5 | 18–23 | publishing, analytics | **manual** upload and manual metric entry; platform APIs need app review (D.3) |
| 6 | 24–30 | affiliate, revenue dashboard | new tables from plan §8 |

Changed decisions: three formats (`comedy`, `facts`, `gadget`) instead of the AI & Technology niche;
format-specific default lengths (27 / 37 / 32 s) instead of 45–60 s; the quality gate judges length against
the job's own target ±20 %. Trend data is entered by hand with a source URL; no platform scraping.
