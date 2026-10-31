# ARCHITECTURE — AI Content Factory (MVP)

**Version:** 0.1 (draft for founder approval)
**Date:** 2026-10-31

---

## 1. Architectural principles

1. **One process, one database, one disk.** At 1–50 jobs/day, distributed infrastructure is
   pure cost. The design must be *splittable* later, not split now.
2. **Everything is a stage over a job.** One uniform contract (`StageInput → StageOutput`)
   for all eleven stages. New capabilities are new stages, not new architecture.
3. **The outside world is behind a port.** LLM, TTS, image, stock-media, web-search and
   platform publishing are all interfaces with swappable adapters. Nothing in the business
   layer imports a vendor SDK.
4. **State lives in the database, not in memory.** Any job can be resumed after a crash or a
   restart. There is no in-memory workflow state that would be lost.
5. **Dependency injection at the edges.** Services receive their providers and repositories;
   nothing reaches for a global. This is what makes the fixture-provider test path possible.
6. **Human approval is a wall, not a signpost.** See §8.

## 2. Layer diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│ PRESENTATION                                                        │
│   app/api/        FastAPI routers (HTTP, no business logic)         │
│   app/ui/         Streamlit operator console (HTTP client only)     │
└───────────────────────────┬──────────────────────────────────────────┘
                            │ DTOs (Pydantic)
┌───────────────────────────▼──────────────────────────────────────────┐
│ APPLICATION SERVICES                                                │
│   JobService         create / query / approve / reject / export      │
│   PipelineService    run or resume a job, run a single stage         │
│   ExportService      bundle assets + manifest                        │
└───────────────────────────┬──────────────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────────────┐
│ ORCHESTRATION                                                       │
│   Orchestrator       stage sequence, resume point, gate enforcement  │
│   StageRunner        timeout, retry, logging, StageRun persistence   │
│   Registry           stage name → Stage implementation               │
└───────────────────────────┬──────────────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────────────┐
│ STAGES (agents and modules; one uniform contract)                   │
│   ResearchAgent  StrategyAgent  ScriptAgent  SeoAgent               │
│   VoiceStage  VisualsStage  SubtitleStage  RenderStage              │
│   QualityStage  ExportStage                                          │
└──────┬─────────────────────────────────────┬─────────────────────────┘
       │ ports                               │ ports
┌──────▼───────────────────────┐  ┌──────────▼─────────────────────────┐
│ AI PROVIDERS (adapters)      │  │ MEDIA PROCESSING                   │
│  LLMProvider                 │  │  FFmpegRenderer                    │
│   ├ ClaudeCliProvider  (dev) │  │  AudioAnalyzer (duration, LUFS)    │
│   ├ AnthropicApiProvider     │  │  SubtitleBuilder (SRT/VTT)         │
│   ├ OllamaProvider           │  │  TitleCardRenderer (Pillow)        │
│   └ FixtureProvider   (test) │  └────────────────────────────────────┘
│  TTSProvider                 │
│   ├ EdgeTtsProvider    (dev) │  ┌────────────────────────────────────┐
│   └ (paid neural TTS)        │  │ EXTERNAL CONTENT                   │
│  ImageProvider               │  │  SearchProvider (web research)     │
│   ├ TitleCardProvider  (dev) │  │  StockMediaProvider (Pexels…)      │
│   └ (paid generation)        │  │  PublishProvider (stubbed, MVP)    │
└──────────────────────────────┘  └────────────────────────────────────┘
       │                                      │
┌──────▼──────────────────────────────────────▼─────────────────────────┐
│ PERSISTENCE                                                          │
│   SQLAlchemy models + repositories   ·   SQLite (WAL)                │
│   FileStore: storage/jobs/{job_id}/{stage}/...                       │
└──────────────────────────────────────────────────────────────────────┘
```

**Dependency rule:** arrows point downward only. A stage never imports an API router; a
provider never imports a service; the persistence layer knows nothing about stages. Enforced
by an import-linter check in CI (week 1), because this is the rule that silently rots first.

## 3. Proposed source tree

```
ai-content-factory/
├─ app/
│  ├─ main.py                  FastAPI app factory, DI container wiring
│  ├─ config.py                Pydantic Settings (env-driven, profiles dev/prod/test)
│  ├─ api/
│  │  ├─ deps.py               FastAPI dependency providers
│  │  └─ routers/              jobs.py  stages.py  assets.py  health.py
│  ├─ schemas/                 request/response DTOs
│  ├─ services/                job_service.py  pipeline_service.py  export_service.py
│  ├─ orchestration/
│  │  ├─ contracts.py          StageInput, StageOutput, Stage protocol
│  │  ├─ runner.py             timeout / retry / logging / persistence
│  │  ├─ orchestrator.py       sequence + resume + gate
│  │  └─ registry.py
│  ├─ stages/
│  │  ├─ research.py  strategy.py  script.py  seo.py
│  │  ├─ voice.py  visuals.py  subtitles.py  render.py
│  │  └─ quality.py  export.py
│  ├─ agents/prompts/          versioned prompt templates (*.md, Jinja2)
│  ├─ providers/
│  │  ├─ base.py               LLMProvider, TTSProvider, ImageProvider, … protocols
│  │  ├─ llm/                  claude_cli.py  anthropic_api.py  ollama.py  fixture.py
│  │  ├─ tts/                  edge_tts.py  fixture.py
│  │  ├─ image/                title_card.py  fixture.py
│  │  ├─ search/               duckduckgo.py  fixture.py
│  │  └─ stock/               pexels.py  fixture.py
│  ├─ media/                   ffmpeg.py  audio.py  subtitles.py  cards.py
│  ├─ quality/                 rules/*.py  gate.py
│  ├─ platforms/               tiktok.py  youtube.py  facebook.py  (stubs + spec docs)
│  ├─ db/                      models.py  repositories/  session.py  migrations/
│  └─ core/                    logging.py  errors.py  retry.py  ids.py  filestore.py
├─ ui/                         Streamlit: Home.py + pages/
├─ tests/                      unit/  integration/  e2e/  fixtures/
├─ storage/                    generated media (git-ignored)
├─ scripts/                    setup_windows.ps1  run_api.ps1  run_ui.ps1  seed_demo.ps1
├─ .env.example
├─ pyproject.toml
├─ PRD.md  ARCHITECTURE.md  DATABASE.md  BACKLOG.md  README.md
```

## 4. The stage contract

Every stage — LLM agent or FFmpeg module — implements one protocol:

```python
class StageInput(BaseModel):
    job_id: str
    stage: StageName
    attempt: int
    language: Language              # vi | en
    payload: dict                   # validated by the stage's own input model
    upstream: dict[StageName, dict] # outputs of prior stages, read-only

class StageOutput(BaseModel):
    status: Literal["ok", "failed", "needs_human"]
    payload: dict                   # validated by the stage's own output model
    artifacts: list[ArtifactRef]    # files written to the FileStore
    metrics: StageMetrics           # duration_ms, tokens_in/out, cost_usd, provider, model
    warnings: list[str]
    error: StageError | None

class Stage(Protocol):
    name: StageName
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    timeout_s: int
    retry: RetryPolicy
    async def run(self, ctx: StageContext, data: StageInput) -> StageOutput: ...
```

The brief requires input schema, output schema, error handling, timeout, retry and logging
*per agent*. Those are not written per agent — they are **declared** per agent and enforced
once in `StageRunner`:

| Requirement | Where it lives |
|---|---|
| Input / output schema | `input_model` / `output_model` on the stage; `StageRunner` validates both and never trusts an LLM response |
| Timeout | `timeout_s` on the stage; `asyncio.wait_for` in the runner |
| Retry | `RetryPolicy(max_attempts, backoff, retry_on)`; exponential backoff with jitter; **only** transient and schema-validation errors are retried, never a content refusal or a quality failure |
| Error handling | Typed `StageError` hierarchy (`ProviderError`, `SchemaError`, `TimeoutError`, `ContentPolicyError`, `MediaError`); unknown exceptions are wrapped, never swallowed |
| Logging | structlog, every record carries `job_id`, `stage`, `attempt`, `provider`, `model`; LLM prompt + raw response saved to the FileStore (not the DB) for audit |
| Traceability | the runner writes one immutable `stage_run` row per attempt before and after execution |

**Why uniform:** eleven stages × six cross-cutting concerns is 66 places to get wrong. This
puts them in one place and makes a new stage a ~60-line file.

### LLM output reliability

Every LLM stage asks for JSON against an explicit schema, then:
1. parse → 2. validate against `output_model` → 3. on failure, one repair attempt that feeds
the validation error back → 4. on second failure, the stage fails with `SchemaError` and the
job goes to the operator. The pipeline never proceeds on a half-parsed LLM response.

## 5. Pipeline flow

```
 INTAKE ─▶ RESEARCH ─▶ STRATEGY ─▶ SCRIPT ─▶ SEO ─┐
                                                  ▼
                        VOICE ─▶ (SUBTITLES ∥ VISUALS) ─▶ RENDER
                                                  │
                                                  ▼
                                              QUALITY
                                                  │
                            ┌─────────────────────┴──────────────┐
                        pass│                                    │fail
                            ▼                                    ▼
                   AWAITING_APPROVAL ◀── operator notes ── NEEDS_REVISION
                     │            │                              │
             approve │            │ reject                       │ re-run stage N+
                     ▼            ▼                              │
                  EXPORTED    NEEDS_REVISION ────────────────────┘
```

Notes:
- `VOICE` runs before `SUBTITLES` and `RENDER` because the audio's real duration and segment
  timings are the source of truth for both. Scripted word counts are an estimate; the
  voice-over is the fact.
- `SUBTITLES` and `VISUALS` are independent and run concurrently (`asyncio.gather`).
- A `QUALITY` failure never silently loops. It parks the job in `NEEDS_REVISION` for a human.
- Resume: the orchestrator reads the latest successful `stage_run` per stage and starts at the
  first stage with no successful run, so a crash or restart costs at most one stage.

## 6. Orchestration: why not LangGraph (yet)

| Need | Plain orchestrator | LangGraph |
|---|---|---|
| Linear sequence with one gate | trivial | works, plus a dependency |
| Durable state the UI can query | SQLite rows the Streamlit UI already reads | its own checkpointer, a second source of truth |
| Resume after crash | query latest `stage_run` | checkpoint restore |
| Human-in-the-loop | a job state + an API call | interrupt/resume primitives |
| Debuggability for a solo founder | read a table | read a graph trace |
| Branching, A/B variants, loops | would get ugly | **clearly better** |

**Decision:** plain orchestrator for the MVP, roughly 200 lines, no new dependency, and
state visible in the same database the UI reads. **Revisit at the end of week 4**: if the
backlog then contains A/B script variants, conditional re-research, or parallel multi-platform
variant generation, adopt LangGraph for the agent half of the pipeline — the stage contract is
deliberately shaped so a `LangGraphOrchestrator` can replace `Orchestrator` without touching
the stages.

## 7. Provider abstraction

```python
class LLMProvider(Protocol):
    name: str
    async def complete(self, req: LLMRequest) -> LLMResponse: ...   # text or JSON-schema'd

class TTSProvider(Protocol):
    name: str
    async def synthesize(self, req: TTSRequest) -> TTSResult: ...   # audio path + segment timings

class ImageProvider(Protocol):
    name: str
    async def generate(self, req: ImageRequest) -> ImageResult: ... # path + licence record
```

Selected by config, never by import:

```
LLM_PROVIDER=claude_cli        # claude_cli | anthropic_api | ollama | fixture
TTS_PROVIDER=edge_tts          # edge_tts | fixture
IMAGE_PROVIDER=title_card      # title_card | fixture
SEARCH_PROVIDER=duckduckgo     # duckduckgo | fixture
STOCK_PROVIDER=pexels          # pexels | none | fixture
```

Each provider reports `cost_usd` per call (zero for the free ones) so the per-video cost in
PRD §6/S6 is measured rather than guessed.

**`ClaudeCliProvider` caveat.** It shells out to `claude -p --output-format json` with a
per-call timeout and a concurrency semaphore of 1. It is marked `dev_only=True`; starting the
app with `APP_ENV=prod` and `LLM_PROVIDER=claude_cli` refuses to boot. That makes the PRD §8
terms position a property of the code, not a note in a document.

**`FixtureProvider`.** Replays recorded responses keyed by a hash of the request. This is what
lets the whole pipeline run in CI with no network, no cost and deterministic output — the
single highest-leverage test decision in this build.

## 8. Enforcing human approval

Three independent layers, because one is not enough for the rule that protects the brand:

1. **Service layer.** `ExportService.export(job)` raises unless
   `job.status == APPROVED and job.approval_id is not None`.
2. **Database.** A `CHECK` constraint: a job may not be in `EXPORTED` without a non-null
   `approval_id`; the `approvals` table requires an `approved_by` and a decision timestamp.
3. **No publisher.** `app/platforms/*` contains interface stubs that raise
   `NotImplementedError("publishing is out of MVP scope")`. There is no code path to a
   platform API, so there is nothing to misconfigure.

## 9. What makes this SaaS-ready later (without building it now)

| Future need | What the MVP already does | What is left to do |
|---|---|---|
| Multi-tenancy | every row has a `tenant_id`, defaulted to `"local"` | add auth, scope queries by tenant |
| Postgres | SQLAlchemy models, no SQLite-specific SQL, Alembic migrations from day one | change the URL, run migrations |
| Object storage | all file access goes through a `FileStore` interface | add an `S3FileStore` |
| Background scale | the pipeline is already async and queue-driven | swap the in-process queue for a real one |
| Per-tenant cost accounting | `stage_run.cost_usd` recorded per call | aggregate and bill |

Total cost of this future-proofing in the MVP: one unused column, one interface, and Alembic.
That is the right amount of speculative work — anything more would be building (c) before (a)
earns anything.

## 10. Deliberate trade-offs (and when each breaks)

| Choice | Breaks when | Then do |
|---|---|---|
| SQLite + WAL | concurrent writers > 1, or > ~500 jobs/day | move to Postgres (models already portable) |
| In-process async worker | a render must survive an app restart mid-flight | a real queue; resume already works per stage |
| Local disk storage | more than one machine, or client-facing links | `S3FileStore` |
| Streamlit UI | the UI needs real interaction, roles or client access | a React front end on the same API |
| Plain orchestrator | branching / A-B / loops appear | LangGraph (§6) |
| No auth | anyone but the founder touches the box | FastAPI auth + tenant scoping |

## 11. Windows specifics

- FFmpeg path from config, not from `PATH` assumptions; probed at startup by `/health` and by
  `scripts/setup_windows.ps1`.
- All paths via `pathlib.Path`; no string concatenation; no path longer than needed, since
  `storage/jobs/{uuid}/...` can approach the 260-character limit with long filenames —
  artifact filenames are therefore short and hashed, not derived from topic text.
- UTF-8 everywhere, explicitly: Vietnamese text in SRT files and in the console. `PYTHONUTF8=1`
  is set in the run scripts because the Windows console default would otherwise mangle
  diacritics.
- `asyncio` default event loop policy is fine on Python 3.12; subprocess calls use
  `asyncio.create_subprocess_exec` with an explicit executable path.
- No symlinks, no `os.fork`, no POSIX-only signal handling.

## 12. Observability

- **Logs:** structlog, JSON to file + human-readable to console, every line carrying `job_id`.
- **Per-job trace:** the `stage_run` table *is* the trace; the UI renders it as a timeline.
- **Cost and latency:** aggregated per job and per stage, shown in the UI; this is how S6 and
  S7 are verified rather than asserted.
- **Prompt audit:** each LLM call's rendered prompt and raw response are written to
  `storage/jobs/{id}/_llm/{stage}-{attempt}.json`. When a script comes out wrong, the first
  question is always "what did we actually ask?" — this answers it.
