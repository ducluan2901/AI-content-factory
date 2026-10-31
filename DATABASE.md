# DATABASE — AI Content Factory (MVP)

**Version:** 0.1 (draft for founder approval)
**Date:** 2026-10-31
**Engine:** SQLite 3 (WAL mode) via SQLAlchemy 2.x + Alembic. Portable to PostgreSQL.

---

## 1. Design rules

1. **`content_job.id` is the spine.** Every row that represents generated content carries it.
   This is the traceability requirement from the brief, made structural.
2. **Append-only where it matters.** `stage_run`, `approval` and `cost_entry` are never
   updated. A re-run adds a row and supersedes the previous one; history is never destroyed.
   This is what lets the operator answer "what did it look like before I rejected it?".
3. **IDs are strings.** `job_` + ULID — sortable by creation time, safe in filesystem paths,
   safe in URLs, and no integer-sequence leakage if this ever becomes multi-tenant.
4. **JSON for stage payloads, columns for anything queried or constrained.** Stage outputs
   evolve weekly; forcing each into columns would mean a migration per prompt change. But
   anything the UI filters on, or the approval gate depends on, is a real column.
5. **`tenant_id` on every content table**, defaulted to `'local'`. One cheap column now
   instead of a rewrite later (ARCHITECTURE.md §9).
6. **Timestamps are UTC, stored as ISO-8601 text** (SQLite has no native timestamp);
   presentation converts to Asia/Ho_Chi_Minh.
7. **Media bytes never go in the database.** Files live in `storage/`; the DB stores a
   relative path, a size and a SHA-256.

## 2. Entity overview

```
tenant (implicit, 'local')
   │
   ▼
content_job ──1:N──▶ stage_run ──1:N──▶ artifact
   │                     │
   │                     └──1:N──▶ cost_entry
   │
   ├──1:N──▶ research_item
   ├──1:1──▶ content_brief
   ├──1:1──▶ script       ──1:N──▶ script_scene
   ├──1:1──▶ seo_package
   ├──1:1──▶ voiceover    ──1:N──▶ voice_segment
   ├──1:N──▶ media_asset          (licence lives here)
   ├──1:1──▶ render_output
   ├──1:1──▶ subtitle_file
   ├──1:N──▶ quality_check
   ├──1:N──▶ approval             (append-only decision log)
   └──1:1──▶ export_bundle
```

## 3. Enumerations

```
job_status      : DRAFT · QUEUED · RUNNING · NEEDS_REVISION · AWAITING_APPROVAL
                · APPROVED · REJECTED · EXPORTED · FAILED · CANCELLED
stage_name      : INTAKE · RESEARCH · STRATEGY · SCRIPT · SEO · VOICE · VISUALS
                · SUBTITLES · RENDER · QUALITY · EXPORT
stage_status    : PENDING · RUNNING · OK · FAILED · TIMEOUT · SKIPPED · NEEDS_HUMAN
language        : vi · en
platform        : tiktok · youtube_shorts · facebook_reels
asset_kind      : stock_video · stock_image · title_card · generated_image · music · other
licence_kind    : pexels · pixabay · cc0 · cc_by · owned · generated · unknown
severity        : info · warning · error
decision        : approved · rejected
```

`job_status` transitions (anything else is rejected by the service layer):

```
DRAFT   → QUEUED → RUNNING
RUNNING → NEEDS_REVISION | AWAITING_APPROVAL | FAILED | CANCELLED
NEEDS_REVISION   → QUEUED | CANCELLED
AWAITING_APPROVAL → APPROVED | REJECTED
REJECTED → NEEDS_REVISION | CANCELLED
APPROVED → EXPORTED
FAILED   → QUEUED | CANCELLED
```

## 4. Tables

### 4.1 `content_job` — the spine

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK | `job_<ULID>` |
| `tenant_id` | TEXT NOT NULL DEFAULT `'local'` | |
| `title` | TEXT | operator-facing label, derived from the topic |
| `input_kind` | TEXT NOT NULL | `topic` \| `product` |
| `input_topic` | TEXT | raw operator input |
| `input_product_desc` | TEXT | raw operator input (business product mode) |
| `language` | TEXT NOT NULL | `vi` \| `en` |
| `target_platforms` | TEXT NOT NULL | JSON array of `platform` |
| `target_duration_s` | INTEGER NOT NULL | default from the format (plan §4.2), CHECK 15..90 |
| `channel_id` | TEXT FK → channel.id | null for jobs submitted without a channel |
| `idea_id` | TEXT FK → content_idea.id | the planner entry this job came from |
| `format` | TEXT NOT NULL DEFAULT `'facts'` | `comedy` \| `facts` \| `gadget` |
| `status` | TEXT NOT NULL | `job_status` |
| `current_stage` | TEXT | `stage_name`, null when idle |
| `profile` | TEXT NOT NULL | provider profile used: `dev` \| `prod` \| `test` |
| `approval_id` | TEXT FK → approval.id | **null until a human approves** |
| `error_summary` | TEXT | last fatal error, for the queue view |
| `total_cost_usd` | REAL NOT NULL DEFAULT 0 | denormalised sum of `cost_entry` |
| `created_at` / `updated_at` | TEXT NOT NULL | UTC ISO-8601 |
| `started_at` / `finished_at` | TEXT | for the S7 latency metric |

Constraints:
- `CHECK (status <> 'EXPORTED' OR approval_id IS NOT NULL)` — **the human-approval wall at
  the storage layer.** PRD D1 / ARCHITECTURE §8.
- `CHECK (target_duration_s BETWEEN 15 AND 90)` — widened from 30 in Sprint 1, since the 30-day plan targets 20–35 s entertainment clips
- Indexes: `(status, created_at DESC)`, `(tenant_id, created_at DESC)`.

### 4.2 `stage_run` — the audit trail (append-only)

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK | `run_<ULID>` |
| `job_id` | TEXT FK → content_job.id ON DELETE CASCADE | |
| `stage` | TEXT NOT NULL | `stage_name` |
| `attempt` | INTEGER NOT NULL | 1-based, counts retries |
| `status` | TEXT NOT NULL | `stage_status` |
| `superseded_by` | TEXT FK → stage_run.id | set when the stage is re-run |
| `input_json` | TEXT | the stage's validated input |
| `output_json` | TEXT | the stage's validated output |
| `provider` / `model` | TEXT | e.g. `anthropic_api` / `claude-haiku-5-5` |
| `tokens_in` / `tokens_out` | INTEGER | |
| `cost_usd` | REAL NOT NULL DEFAULT 0 | |
| `duration_ms` | INTEGER | |
| `error_type` / `error_message` | TEXT | typed `StageError` class name + message |
| `warnings_json` | TEXT | JSON array |
| `log_path` | TEXT | relative path to the prompt/response audit file |
| `started_at` / `finished_at` | TEXT | |

- `UNIQUE (job_id, stage, attempt)`
- Index `(job_id, stage, attempt DESC)` — the resume query and the UI timeline both use it.
- **Never UPDATE except to set `superseded_by`.** The resume point is
  "the newest `OK` row per stage with `superseded_by IS NULL`".

### 4.3 `research_item`

| Column | Type | Notes |
|---|---|---|
| `id` TEXT PK, `job_id` FK, `stage_run_id` FK | | |
| `claim` | TEXT NOT NULL | the fact, as it will be used |
| `detail` | TEXT | supporting context |
| `source_url` | TEXT | null ⇒ must be `verified = 0` |
| `source_title` / `source_publisher` | TEXT | |
| `published_at` | TEXT | recency matters in the AI niche |
| `verified` | INTEGER NOT NULL DEFAULT 0 | 1 only with a source URL |
| `relevance` | REAL | 0..1, agent's own score |
| `used_in_script` | INTEGER NOT NULL DEFAULT 0 | set by the script stage |

`CHECK (verified = 0 OR source_url IS NOT NULL)` — enforces PRD D6. A claim cannot be marked
verified without a source.

### 4.4 `content_brief` (1:1)

`id`, `job_id` UNIQUE, `stage_run_id`, `angle`, `hook_concept`, `target_audience`,
`pain_point`, `promise`, `cta`, `tone`, `keywords_json`, `scene_count`,
`target_duration_s`, `rationale`.

### 4.5 `script` (1:1) and `script_scene` (1:N)

`script`: `id`, `job_id` UNIQUE, `stage_run_id`, `version` INTEGER, `full_text`,
`hook_text`, `cta_text`, `word_count`, `syllable_count`, `estimated_duration_s`,
`edited_by_human` INTEGER DEFAULT 0, `created_at`.

> `syllable_count` rather than `word_count` drives the Vietnamese duration estimate —
> Vietnamese is monosyllabic and word counts mislead by 30–40 % (PRD D4). `estimated_duration_s`
> is an estimate only; `voiceover.duration_s` is the truth.

`script_scene`: `id`, `script_id` FK, `index` INTEGER, `narration` TEXT,
`on_screen_text` TEXT, `visual_direction` TEXT, `estimated_duration_s` REAL,
`research_item_ids_json` TEXT — the per-scene link back to sources, which is what makes the
traceability in PRD S3 actually usable. `UNIQUE (script_id, index)`.

### 4.6 `seo_package` (1:1 per job, per-platform variants inside)

`id`, `job_id` UNIQUE, `stage_run_id`, `variants_json` — a JSON object keyed by platform,
each `{title, caption, hashtags: [...], description}` — plus `primary_title`,
`primary_caption`, `hashtags_json` for the common case, and `char_counts_json` so the quality
gate can check each platform's limit without re-parsing.

### 4.7 `voiceover` (1:1) and `voice_segment` (1:N)

`voiceover`: `id`, `job_id` UNIQUE, `stage_run_id`, `provider`, `voice_id`, `language`,
`rate`, `pitch`, `audio_path`, `audio_format`, `sample_rate`, `duration_s` REAL NOT NULL,
`loudness_lufs` REAL, `sha256`, `bytes`.

`voice_segment`: `id`, `voiceover_id` FK, `index`, `scene_index`, `text`,
`start_ms` INTEGER, `end_ms` INTEGER. `UNIQUE (voiceover_id, index)`,
`CHECK (end_ms > start_ms)`.

> These segments are the single source of timing for **both** the SRT and the visual cuts.
> One timing source means audio and subtitles cannot drift apart.

### 4.8 `media_asset` — the licence ledger

| Column | Type | Notes |
|---|---|---|
| `id` TEXT PK, `job_id` FK, `stage_run_id` FK | | |
| `kind` | TEXT NOT NULL | `asset_kind` |
| `scene_index` | INTEGER | null for a global asset such as music |
| `order_index` | INTEGER | position in the timeline |
| `path` | TEXT NOT NULL | relative to `storage/` |
| `width` / `height` / `duration_s` | | |
| `sha256` / `bytes` | TEXT / INTEGER | |
| `source_provider` | TEXT NOT NULL | e.g. `pexels`, `title_card` |
| `source_url` / `source_id` | TEXT | |
| `author` | TEXT | |
| `licence` | TEXT NOT NULL | `licence_kind` |
| `licence_url` | TEXT | |
| `attribution_required` | INTEGER NOT NULL DEFAULT 0 | |
| `attribution_text` | TEXT | |
| `prompt` | TEXT | for generated assets |

`CHECK (licence <> 'unknown')` is **not** applied at the DB level — an unknown licence must be
*recordable* so the quality gate can report it. The gate (`asset_licence` rule, severity
`error`) is what blocks the job. PRD D3.

### 4.9 `render_output` (1:1)

`id`, `job_id` UNIQUE, `stage_run_id`, `video_path`, `container`, `video_codec`,
`audio_codec`, `width` (1080), `height` (1920), `fps`, `duration_s`, `bitrate_kbps`,
`bytes`, `sha256`, `subtitles_burned` INTEGER, `thumbnail_path`,
`ffmpeg_command` TEXT — the exact command line, kept because reproducing a render problem
without it is guesswork.

### 4.10 `subtitle_file` (1:N by format)

`id`, `job_id`, `stage_run_id`, `format` (`srt` \| `vtt`), `language`, `path`, `cue_count`,
`max_chars_per_line`, `max_cue_duration_s`, `sha256`.
`UNIQUE (job_id, format, language)`.

### 4.11 `quality_check` (1:N)

| Column | Notes |
|---|---|
| `id`, `job_id`, `stage_run_id` | |
| `rule` | e.g. `duration_window`, `loudness`, `subtitle_readability`, `asset_licence`, `claim_grounding`, `platform_limits`, `safety_terms`, `aspect_ratio`, `audio_present` |
| `severity` | `info` \| `warning` \| `error` |
| `passed` | INTEGER NOT NULL |
| `expected` / `actual` | TEXT |
| `message` | TEXT — operator-facing, in plain language |
| `created_at` | |

Gate rule: **any `error` that did not pass ⇒ the job goes to `NEEDS_REVISION`.** Warnings are
shown at the approval screen but do not block. Index `(job_id, severity)`.

### 4.12 `approval` (append-only)

`id`, `job_id` FK, `decision` (`approved` \| `rejected`), `approved_by` TEXT NOT NULL,
`notes` TEXT, `quality_warnings_acknowledged_json` TEXT, `created_at` TEXT NOT NULL.

- No UPDATE, no DELETE. A changed mind is a new row.
- `content_job.approval_id` points at the latest `approved` row and is the value the
  `EXPORTED` check constraint depends on.
- `approved_by` is not nullable: an approval always has a name attached, which is what makes
  it an approval rather than a flag.

### 4.13 `export_bundle` (1:1)

`id`, `job_id` UNIQUE, `stage_run_id`, `bundle_dir`, `manifest_path`, `zip_path`,
`file_count`, `bytes`, `created_at`.

### 4.14 `cost_entry` (append-only)

`id`, `job_id`, `stage_run_id`, `provider`, `model`, `unit` (`tokens` \| `chars` \|
`seconds` \| `call`), `quantity` REAL, `unit_cost_usd` REAL, `cost_usd` REAL, `created_at`.

Exists separately from `stage_run.cost_usd` because one stage can make several provider calls
(research = N searches + 1 LLM call), and PRD S6 needs the per-provider breakdown to know
*what* costs money.

### 4.15 `prompt_version` (small, but it pays for itself)

`id`, `stage` TEXT, `name` TEXT, `version` INTEGER, `template_sha256` TEXT,
`template_path` TEXT, `created_at`. `UNIQUE (stage, name, version)`.

`stage_run` stores `prompt_version_id`. When output quality changes between two jobs, the
first question is which prompt ran — this answers it without git archaeology.

### 4.16 `channel` — Sprint 1 (30-day plan §2.1)

`id` (`ch_…`), `tenant_id`, `name`, `market` (`vn` | `global`), `language`, `positioning`, `voice_id` (overrides
the per-language TTS voice), `handles_json` (`{platform: handle}`), `status` (`active` | `paused`), `is_sample`,
`created_at`, `updated_at`.

### 4.17 `trend` — Trend Radar (30-day plan §7.4)

`id` (`tr_…`), `tenant_id`, `title`, `summary`, `source` (`manual` | `sample` | site name), `source_url`,
`observed_at`, `market`, `language`, `format`, the five score components `velocity`,
`retention_potential`, `channel_fit`, `monetization_potential`, `production_feasibility` (REAL, NULL when
unknown, `CHECK 0..100` each), `trend_score` (derived), `confidence` (0–1, the weight share of components
actually provided), `is_estimated`, `status` (`new` | `reviewed` | `selected` | `rejected`), `notes`,
`is_sample`, timestamps. Index `(status, trend_score)`.

The score is recomputed by the service on every write, never by hand: a missing component counts as neutral
50 and lowers `confidence`, so an incomplete trend can never outrank a measured one by accident. Sample rows
(`is_sample = 1`, `source = 'sample'`) are illustrative and the UI labels them as such.

### 4.18 `content_idea` — Content Planner

`id` (`idea_…`), `tenant_id`, `channel_id` FK, `trend_id` FK (nullable), `title`, `format`, `hook`,
`premise`, `product_desc` (gadget format: the only product facts the script may use), `target_duration_s`
(`CHECK 15..90`, default from the format), `viral_score`, `revenue_score` (`CHECK 0..100`), `status`
(`backlog` | `selected` | `scripted` | `dropped`), `notes`, `is_sample`, timestamps.

Creating a job from an idea copies the channel's language and the idea's format, duration and product facts
onto the job, sets the idea to `scripted`, and moves a `new`/`reviewed` trend to `selected`. That is the
traceability chain the plan asks for: trend → idea → job → video.

## 5. Filesystem layout (mirrors the DB)

```
storage/
└─ jobs/{job_id}/
   ├─ research/sources.json
   ├─ script/script-v{n}.json
   ├─ voice/vo.mp3 · segments.json
   ├─ visuals/{order}-{sha8}.{ext} · cards/{n}.png
   ├─ subtitles/vi.srt · vi.vtt
   ├─ render/final.mp4 · thumb.jpg
   ├─ export/manifest.json · bundle.zip
   └─ _llm/{stage}-{attempt}.json        prompt + raw response audit
```

Short, hashed filenames: Windows `MAX_PATH` is 260 characters and a topic-derived Vietnamese
filename under a UUID directory gets close to it (ARCHITECTURE.md §11).

## 6. Migrations

Alembic from the first commit, even on SQLite, even solo. SQLite cannot drop or alter a
column, so an ad-hoc schema becomes unmigratable data within a week. Every schema change is a
revision; `alembic upgrade head` runs on app start in `dev`.

In code: `app.db.migrate()` runs at API startup for every profile except `test`. A database that the
week-1 code created with `create_all` is stamped at the baseline revision instead of re-created. Tests build
throwaway schemas directly. New revision: `.\.venv\Scriptslembic.exe revision --autogenerate -m "..."`.

## 7. Retention

- Jobs and all metadata: kept indefinitely (it is the training and review corpus).
- Media files for `REJECTED` / `FAILED` jobs: a `scripts/prune_storage.ps1` job deletes
  rendered video and stock downloads older than 30 days while keeping all DB rows and the
  `_llm` audit files. Video is what fills the disk; the metadata is what has value.
