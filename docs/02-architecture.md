# 02 — Architecture

Version 1.0 · Applies to `backend/` and `mobile/`

---

## 1. Principles

1. **Dependencies point inward.** `domain` knows nothing. `application` knows
   `domain`. `infrastructure` and `api` know `application` and `domain`. Nothing
   inner imports anything outer. A violation is a CI failure, not a style
   opinion.
2. **Feature-first, layer-second.** Code is grouped by what it does for the user
   (`projects`, `vision`, `guides`), and only inside a feature by layer. This
   keeps changes local and makes deletion possible.
3. **Ports and adapters at every external edge.** Model providers, storage,
   cache, queue, and third-party APIs are all behind protocols defined by the
   domain.
4. **Explicit composition.** One DI container wires adapters to ports at
   startup. No service locators, no import-time singletons, no hidden globals.
5. **Data in, data out.** Application services take and return typed DTOs.
   ORM models never cross the application boundary; Pydantic schemas never leak
   into the domain.

## 2. Backend layers

```
app/
├── core/                 cross-cutting, framework-adjacent
│   ├── config.py         typed settings (pydantic-settings)
│   ├── container.py      DI container: builds adapters, exposes providers
│   ├── security.py       password hashing, JWT issue/verify
│   ├── logging.py        structured JSON logging + request correlation id
│   ├── errors.py         AppError hierarchy → HTTP envelope mapping
│   └── ratelimit.py      Redis token bucket
│
├── api/v1/               HTTP adapter (the only place FastAPI appears)
│   ├── router.py         aggregates feature routers
│   ├── deps.py           request-scoped dependencies (current user, services)
│   └── routes/           auth, projects, media, analyses, guides, chat, advice, account
│
├── features/<feature>/
│   ├── domain/           entities, value objects, ports, domain rules (pure)
│   ├── application/      services, use cases, DTOs (orchestration, no I/O)
│   └── data/             repository implementations, ORM models, mappers
│
├── infrastructure/
│   ├── db/               engine, session, base, unit of work
│   ├── ai/               provider gateway + adapters (openai/anthropic/gemini/fake)
│   ├── storage/          local / s3 / supabase adapters behind StoragePort
│   ├── cache/            Redis adapter behind CachePort
│   ├── imaging/          deterministic annotation renderer (Pillow)
│   └── jobs/             Celery app + task definitions
│
└── shared/               value objects used by more than one feature
```

### 2.1 Features and their responsibilities

| Feature | Owns | Key ports it depends on |
| --- | --- | --- |
| `auth` | Users, credentials, tokens, sessions, consent records | `UserRepository`, `TokenService` |
| `media` | Upload intake, validation, compression metadata, signed URLs, perceptual hashing | `StoragePort`, `MediaRepository` |
| `vision` | Analysis entity, confidence gating, extra-photo requests | `VisionPort`, `AnalysisRepository`, `CachePort` |
| `guides` | Guide entity, revisions, step progress, schema validation | `GuideGeneratorPort`, `GuideRepository` |
| `annotation` | Per-step visual instructions | `ImageAnnotatorPort` (deterministic) + `ImageEditPort` (generative, optional) |
| `projects` | Project lifecycle, orchestration of the whole flow, clarification loop | all of the above via application services |
| `chat` | Conversations, messages, streaming replies grounded in project context | `ChatPort`, `ConversationRepository` |
| `knowledge` | Materials, fasteners, product classes, manuals, embeddings + retrieval | `EmbeddingPort`, `KnowledgeRepository` |
| `safety` | Deterministic classification rules, red lines, referral copy | `KnowledgeRepository` (rules + citations) |
| `advice` | Pre-purchase recommendation workflow | `ChatPort`, `KnowledgeRepository` |
| `billing` | Entitlements, quota, credit ledger (stubbed in v1, real in v2) | `EntitlementRepository` |
| `privacy` | Export, deletion, audit log, consent state | all repositories via a coordinator |

### 2.2 Ports (the contract surface)

Defined in `features/*/domain/ports.py`, all `typing.Protocol`, all async:

```python
class VisionPort(Protocol):
    async def analyze(self, req: VisionRequest) -> VisionResult: ...

class GuideGeneratorPort(Protocol):
    async def generate(self, req: GuideRequest) -> GuideDraft: ...

class ChatPort(Protocol):
    def stream(self, req: ChatRequest) -> AsyncIterator[ChatChunk]: ...

class EmbeddingPort(Protocol):
    async def embed(self, texts: Sequence[str]) -> list[Vector]: ...

class ImageAnnotatorPort(Protocol):
    async def annotate(self, image: bytes, ops: Sequence[AnnotationOp]) -> bytes: ...

class StoragePort(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> StoredObject: ...
    async def signed_url(self, key: str, ttl: timedelta) -> str: ...
    async def delete(self, key: str) -> None: ...

class CachePort(Protocol):
    async def get(self, key: str) -> bytes | None: ...
    async def set(self, key: str, value: bytes, ttl: timedelta) -> None: ...
```

**The rule that keeps this honest:** `grep -r "import openai" app/ --include="*.py"`
must only match `app/infrastructure/ai/`. CI enforces it.

### 2.3 The AI provider gateway

```
application service
        │  (domain types only)
        ▼
AIGateway  ── selects adapter by capability + config + fallback order
        │  ── enforces cost ceiling, timeout, retry-with-backoff
        │  ── records AiCall(provider, model, prompt_version, tokens, cost, latency)
        ▼
OpenAIAdapter │ AnthropicAdapter │ GeminiAdapter │ FakeAdapter
```

Every adapter is responsible for translating domain request objects into its own
wire format and, critically, for **validating the response against the domain
schema before returning it**. An adapter that cannot produce a valid domain
object raises `ProviderContractError`; the gateway retries once with a repair
prompt, then fails the job cleanly.

Capabilities are declared, not assumed:

```python
class Capability(StrEnum):
    VISION = "vision"
    STRUCTURED_OUTPUT = "structured_output"
    STREAMING = "streaming"
    IMAGE_EDIT = "image_edit"
    EMBEDDING = "embedding"
```

The gateway resolves `(capability, quality tier)` → adapter, so a deployment can
use one provider for vision and another for text without any feature knowing.

### 2.4 Jobs

Long AI work never blocks a request.

```
POST /v1/projects/{id}/analyze   → 202 { job_id, status: "queued" }
GET  /v1/jobs/{job_id}           → status | result | error
GET  /v1/jobs/{job_id}/events    → SSE progress stream
```

Jobs are rows in Postgres (source of truth, survives worker restarts) executed
by Celery workers with Redis as broker. Idempotency: a job carries an
`idempotency_key`; re-submitting the same key returns the existing job.

### 2.5 Unit of work / transactions

One `AsyncSession` per request, provided by DI, committed once by the
`UnitOfWork` at the end of a successful use case. Repositories never commit.
Background jobs create their own session per task.

## 3. Mobile architecture (Flutter)

```
lib/
├── main.dart
├── app.dart                    root widget, theme, router wiring
├── core/
│   ├── config/                 env (dart-define), feature flags
│   ├── theme/                  design tokens, ThemeData light+dark, motion
│   ├── router/                 GoRouter config, typed routes, guards
│   ├── network/                Dio client, auth interceptor, retry, error mapping
│   ├── storage/                secure token store, offline cache (Isar/Hive)
│   ├── errors/                 Failure hierarchy + user-facing messages
│   └── di/                     Riverpod providers for infrastructure
├── features/<feature>/
│   ├── domain/                 entities, repository interfaces
│   ├── data/                   DTOs (freezed/json_serializable), remote+local sources
│   ├── application/            Riverpod notifiers, state classes
│   └── presentation/           screens, widgets (no business logic)
└── shared/
    ├── design_system/          Buttons, Cards, Sheets, Typography, Motion
    └── widgets/                composite reusable widgets
```

- **State:** Riverpod (code-generated). One notifier per screen-level concern;
  `AsyncValue` for anything that loads.
- **Navigation:** GoRouter with typed routes and an auth guard. Deep links map to
  project/step so a push notification can open the exact step.
- **Networking:** Dio + interceptors (auth, refresh-on-401, correlation id,
  retry with jitter on idempotent verbs). Generated DTOs from the OpenAPI schema.
- **Offline:** repository-level cache-then-network for reads; a small outbox for
  step-completion writes so progress survives connectivity loss.
- **No business logic in widgets.** Widgets read state and emit intents.

## 4. Cross-cutting concerns

| Concern | Mechanism |
| --- | --- |
| Correlation | `X-Request-ID` accepted or generated; attached to logs, jobs, AI calls, and Sentry events; surfaced in the app's debug screen |
| Config | `pydantic-settings` on the backend, `--dart-define` on mobile. No config read from a mutable file at runtime |
| Feature flags | DB-backed table + Redis cache, read through `FeatureFlagPort`; used to dark-launch AR, providers, and paid tiers |
| Observability | Structured JSON logs, Sentry (both sides), Prometheus-compatible `/metrics`, per-job cost and latency histograms |
| Testing | `fake` adapters for every port; unit tests are pure and offline; integration tests use a real Postgres via docker-compose |
| Migrations | Alembic, one migration per PR, forward-only, reviewed for lock behaviour |

## 5. Deployment topology

```
             ┌─────────────┐
 App ──HTTPS─▶ CDN / WAF   │
             └──────┬──────┘
                    ▼
            ┌───────────────┐        ┌──────────────┐
            │ API (uvicorn) │◀──────▶│ Redis        │
            │ N replicas    │        │ cache/broker │
            └───┬───────┬───┘        └──────┬───────┘
                │       │                   │
                ▼       ▼                   ▼
        ┌────────────┐ ┌──────────────┐ ┌──────────────┐
        │ Postgres   │ │ Object store │ │ Celery       │
        │ + pgvector │ │ (images)     │ │ workers      │
        └────────────┘ └──────────────┘ └──────────────┘
```

Stateless API containers; all state in Postgres, Redis, or object storage.
Health endpoints: `/health/live` (process) and `/health/ready` (dependencies).

## 6. Extension points for the roadmap

| Future feature | Extension point — no refactor needed |
| --- | --- |
| AR guidance | `Guide.steps[].spatial_anchors` already reserved in the guide schema; AR renderer consumes it |
| Live video assistance | `ChatPort.stream` accepts a frame source; a new adapter handles realtime |
| Shopping list / affiliate | `Guide.materials[]` items are attribute-specified product classes; a `CatalogPort` maps them to SKUs |
| Multiple homes / rooms | `projects.site_id` FK to a `sites` table already in the schema |
| AI memory | `user_memory` table + `MemoryPort` consulted during prompt assembly |
| Professional mode | Role already on the user; entitlement checks already wrap generation |
| Offline authoring | Outbox pattern already used for step progress; extend to project creation |
| Wearables / glasses | Clients talk to the same v1 API; nothing in the API assumes a phone |
