# PRD — AI Content Factory (MVP)

**Version:** 0.1 (draft for founder approval)
**Date:** 2026-10-31
**Status:** Awaiting approval. No application code written yet.

---

## 1. Problem

Producing short-form video for TikTok / YouTube Shorts / Facebook Reels is a repetitive
assembly line: research → angle → script → voice → visuals → render → caption → publish.
A human operator spends 2–4 hours per video, and most of that time is not creative — it is
assembly. For a channel that needs 1–3 videos per day, and for an agency serving several
business clients, that cost does not scale.

## 2. Product in one sentence

Give the system a topic or a product description; get back a ready-to-publish vertical MP4
plus its title, caption, hashtags and SRT — with a human approving before anything ships.

## 3. Business context

| Item | Decision |
|---|---|
| Primary market | Vietnam (Vietnamese-language content) |
| Secondary | English content, as an experiment |
| First niche | AI & Technology |
| Platforms | TikTok, YouTube Shorts, Facebook Reels |
| Revenue model | (a) own media channels, (b) done-for-you AI content service for businesses, (c) future SaaS |
| Cost constraint | Minimize external API spend; founder has a Claude Code Max subscription |

**Why this ordering matters for the MVP:** (a) and (b) are *internal-operator* products. The
operator is us. That means the MVP does **not** need multi-tenancy, billing, user accounts or
a polished UI. It needs throughput and reliability. SaaS concerns (c) are explicitly
deferred — but the data model and the provider abstraction are designed so they will not have
to be thrown away later (see ARCHITECTURE.md §9).

## 4. Users and jobs

### U1 — Content Operator (primary; that is us)
- Submit a topic, come back later, review, approve or reject, export.
- Needs to see *why* a script says what it says (traceability) in order to trust it.
- Needs to fix a bad script without re-running the whole pipeline.

### U2 — Agency client (secondary, read-only in the MVP)
- Receives the exported asset bundle. Does **not** log into the system in the MVP.
- Delivery is a folder / zip, not a portal.

### U3 — Channel manager (future)
- Scheduling, calendar, performance feedback loop. **Out of MVP scope.**

## 5. MVP scope

### 5.1 In scope

The pipeline, as stages over one `ContentJob`:

| # | Stage | Output |
|---|---|---|
| 1 | Intake | validated topic / product brief, language, platform targets |
| 2 | Research | structured facts with source URLs, each claim flagged verified / unverified |
| 3 | Strategy | content brief: angle, hook, audience, CTA, tone, target duration |
| 4 | Script | 45–60s script: hook / body beats / CTA, with per-scene visual direction |
| 5 | SEO | title, caption, hashtags, per-platform variants |
| 6 | Voice | voice-over audio + segment timings |
| 7 | Visuals | ordered scene assets (stock clips/images + generated title cards) with licence record |
| 8 | Render | 1080×1920 MP4, H.264/AAC |
| 9 | Subtitles | SRT (and VTT) aligned to the voice-over |
| 10 | Quality | automated gate: duration, loudness, readability, safety, asset licences |
| 11 | **Human approval** | operator approves / rejects with notes — **mandatory, blocking** |
| 12 | Export | asset bundle on disk + manifest JSON |

Plus:
- FastAPI service: submit job, poll status, read artifacts, approve/reject, re-run a single stage.
- Streamlit operator console: queue, stage-by-stage inspector, video preview, approve/reject.
- SQLite persistence; every artifact traceable to a `ContentJob.id`.
- Provider abstraction for LLM / TTS / image so a provider is swappable by config.
- Runs on Windows.

### 5.2 Explicitly out of scope for the MVP

- **Automatic publishing to any platform.** The MVP exports; a human uploads.
  Platform integration is designed for and stubbed, not implemented (see §7, D1).
- Multi-tenant accounts, auth, billing, quotas.
- Avatar / talking-head / lip-sync video.
- Automated music-bed licensing (one hand-cleared track slot, selected manually).
- Analytics ingestion and a performance feedback loop.
- Kubernetes, Kafka, microservices, Redis, Celery. A single process with an in-process
  worker is sufficient at 1–50 jobs/day.

## 6. Success criteria

The MVP is **done** when, on a clean Windows machine following README setup only:

1. **S1 — End to end.** `POST /jobs` with the topic *"Tại sao AI agent sẽ thay đổi cách làm
   việc năm 2026"* produces, without human intervention up to the approval gate, a 45–60s
   1080×1920 MP4 with Vietnamese voice-over and correct Vietnamese subtitles.
2. **S2 — The quality gate works.** A deliberately broken job (e.g. a 20s script) is caught
   by the quality module and surfaced to the operator, not silently shipped.
3. **S3 — Traceability.** For a finished video the operator can read, in the UI, every
   stage's input and output, the research sources, and the licence of every visual asset.
4. **S4 — Approval is mandatory.** No export path exists that bypasses human approval.
5. **S5 — Partial re-run.** The operator can reject the script, edit it, and re-run from
   stage 4 onward without redoing research.
6. **S6 — Cost.** Marginal external API cost per video ≤ **$0.10**; target $0.00 in the
   development configuration. See §8.
7. **S7 — Latency.** p50 wall-clock from submit to awaiting-approval ≤ **10 minutes**.

Deliberately *not* success criteria: view counts, engagement, or "the script is good".
Content quality is judged by the operator at the approval gate; the system's job is to make a
reviewable candidate cheaply and repeatably.

## 7. Key product decisions and their justification

**D1 — Human approval is a database state, not a UI convention.**
A job cannot reach `EXPORTED` except through `AWAITING_APPROVAL → APPROVED`. Enforced in the
service layer *and* by a DB-level check, so no future feature can accidentally automate it.

**D2 — Stages are resumable and individually re-runnable.**
Each stage writes an immutable `stage_run` row with its input, output, provider, model, cost
and duration. Re-running a stage supersedes rather than overwrites. This is what makes S3 and
S5 possible, and it is cheap to build on day one and expensive to retrofit.

**D3 — A visual asset carries a licence record or it does not ship.**
Every asset row stores source, licence and attribution requirement. The quality gate fails a
job that contains an unlicensed asset. This is the one compliance rule that protects the
agency revenue line: a client claim over a stock photo would cost more than the whole MVP.

**D4 — Vietnamese first, and that is a real technical constraint, not a locale string.**
Vietnamese TTS quality, Vietnamese subtitle line-breaking (diacritics, no mid-syllable
breaks) and Vietnamese reading speed (~2.5–3 syllables/sec for natural delivery) are not the
English defaults. Script-length targets and subtitle rules are language-parameterised from
day one.

**D5 — LangGraph is deferred, not rejected.**
The pipeline is a linear DAG with one human gate. A plain orchestrator over `stage_run` rows
covers it with less machinery and keeps state in SQLite where the UI can already see it.
LangGraph earns its place when we add branching (A/B script variants, conditional
re-research) — a week-5 decision, revisited then. See ARCHITECTURE.md §6.

**D6 — Research is grounded or it is labelled.**
Every factual claim in a script traces to a research item with a source URL, or it is marked
`unverified` and the quality gate warns. In the AI/tech niche, a confidently wrong claim is
the fastest way to lose a channel's credibility.

## 8. The cost constraint — honest position

The brief says: *minimize external API costs; the founder has Claude Code Max.*

**This needs a frank flag before any code is written.** A Claude Code Max subscription is an
interactive developer tool. Wiring it as the inference backend of a product that serves
paying clients is outside what that subscription is for, and a server that shells out to the
`claude` CLI per job is both a terms risk and an operational one: interactive auth, no
concurrency guarantees, no SLA, no per-call cost accounting.

The architecture therefore treats the LLM as a **swappable provider** with three
configurations:

| Config | Provider | Use | Marginal cost |
|---|---|---|---|
| `dev` | local headless `claude -p`, or a local Ollama model | development, prompt iteration, our own channels | $0 |
| `prod` | Anthropic API (Haiku/Sonnet class) | client work, future SaaS | ~$0.01–0.05 / video |
| `test` | recorded-fixture provider | CI, offline tests | $0 |

The same three-way split applies to TTS (free `edge-tts` → paid neural TTS) and to images
(free stock + generated cards → paid generation).

**Recommendation:** develop on `dev` so development stays free, and switch the client-facing
line to `prod` as soon as a video earns money. A video that earns even $1 pays for 20–100
generations; the subscription-as-backend saving is real but small, and it is not a foundation
to build a business on. The founder decides; the code supports either without a rewrite.

## 9. Assumptions

- **A1** Single operator, ≤ 50 jobs/day, single machine. If wrong, the SQLite / in-process
  choice breaks and that is a deliberate, documented trade (ARCHITECTURE.md §10).
- **A2** FFmpeg and Python 3.12 can be installed on the operator machine. **Neither is
  present on this machine today** — see README.md.
- **A3** Stock footage comes from a free-tier API with a permissive licence (Pexels /
  Pixabay) or from generated title cards. No scraped media.
- **A4** Research is web-search based; no paid data vendor.
- **A5** Publishing stays manual through the MVP, so no platform API approval is on the
  critical path.

## 10. Open questions for the founder

1. **Cost and terms (§8):** approve the `dev`-for-us / `prod`-for-clients split?
2. **Visuals:** is it acceptable for the MVP to ship stock footage + generated title cards,
   with no AI image generation? It is the cheapest credible path and removes a GPU dependency.
3. **Voice:** is a free Microsoft Edge Vietnamese neural voice acceptable for the first
   videos, or does the brand need a distinctive / cloned voice from the start?
4. **Client deliverable:** is a zip folder on disk enough, or is a shared drive / link needed
   in the MVP?
5. **Scope:** confirmed that automatic publishing is out, i.e. the MVP ends at export?
