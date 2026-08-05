# HandyAI

> AI-powered handyman assistant. Photograph what you want to install, repair or
> buy — get a personalized, safety-checked guide for **your** wall, **your**
> hardware, **your** home.

**Status:** Phase 1 complete (architecture + specification), Phase 2 in progress
(backend + mobile foundations). See [Progress Log](#progress-log) for the
authoritative, chronological record of what exists today.

---

## מצב הפרויקט (סיכום בעברית)

מסמך זה הוא נקודת האמת של הפרויקט. כל שלב מתועד ב־[Progress Log](#progress-log)
כך שניתן להמשיך את הפיתוח מכל מכונה או פלטפורמה אחרת בלי הקשר נוסף.

- `docs/` — כל המפרט הטכני: ארכיטקטורה, סכמת מסד נתונים, API, design system,
  צינור ה־AI, מדיניות בטיחות, פרטיות ורגולציה, ומפת דרכים.
- `backend/` — שרת FastAPI (Python) בארכיטקטורה נקייה, כולל בדיקות.
- `mobile/` — אפליקציית Flutter.
- כדי להתחיל לעבוד: קרא את [Quick Start](#quick-start) ואז את
  `docs/10-progress-log.md`.

**חשוב:** Flutter SDK לא מותקן בסביבת הפיתוח המרוחקת שבה נכתב הקוד, לכן קוד
המובייל נכתב לפי תקן production אך **הקומפילציה והריצה מתבצעות אצלך / ב־CI**.
הבקאנד לעומת זאת נבדק ורץ בפועל.

---

## Table of contents

- [Product vision](#product-vision)
- [Repository layout](#repository-layout)
- [Documentation index](#documentation-index)
- [Quick start](#quick-start)
- [Architecture at a glance](#architecture-at-a-glance)
- [Engineering standards](#engineering-standards)
- [Environment variables](#environment-variables)
- [Roadmap](#roadmap)
- [Progress log](#progress-log)

---

## Product vision

Home improvement help today is generic: a YouTube video of *someone else's*
wall, a PDF manual for a product you may not own, a forum thread from 2013.
HandyAI inverts that. The user shows their actual situation — the TV, the mount,
the wall, the room — and the system produces a guide that is specific to it:
correct anchors for that wall material, correct VESA plate, the tools they need,
the risks that apply, and step-by-step instructions annotated **on their own
photos**.

Three entry workflows:

| Workflow | User intent | Output |
| --- | --- | --- |
| **Install** | "Mount this TV" | Personalized installation guide |
| **Repair** | "This faucet drips" | Diagnosis + repair guide |
| **Advise** | "I bought a 75\" TV, what mount?" | Compatibility + shopping recommendation |

Across all three, the same principles hold:

1. **Ask the minimum.** The vision pipeline extracts everything it can; the
   assistant only asks what it genuinely cannot see.
2. **Safety outranks helpfulness.** Every task carries a safety
   classification, and low confidence produces a request for another photo —
   never a guess.
3. **Show, don't tell.** Steps are annotated on the user's images wherever
   possible; synthetic illustrations are a fallback, not the default.

## Repository layout

```
Handy_Man_APP/
├── README.md                  # ← you are here: master document
├── docs/                      # full technical specification (read in order)
├── backend/                   # FastAPI service (Clean Architecture)
│   ├── app/
│   │   ├── core/              # config, security, logging, errors, DI container
│   │   ├── api/v1/            # HTTP layer: routers, dependencies, schemas
│   │   ├── features/          # feature-first modules (see docs/02)
│   │   ├── infrastructure/    # DB, storage, cache, queue, AI providers
│   │   └── shared/            # cross-cutting value objects & utilities
│   ├── alembic/               # database migrations
│   ├── tests/                 # unit + integration tests
│   └── docker-compose.yml     # Postgres + pgvector + Redis for local dev
├── mobile/                    # Flutter application
│   ├── lib/
│   │   ├── core/              # theme, router, DI, network, errors
│   │   ├── features/          # feature-first modules mirroring backend
│   │   └── shared/            # design-system widgets
│   └── test/
└── .github/workflows/         # CI: lint, test, build
```

## Documentation index

Read these in order. They are the design authority — if code and docs disagree,
that is a bug in one of them, and the fix includes updating the doc.

| Doc | Contents |
| --- | --- |
| [`docs/01-technical-specification.md`](docs/01-technical-specification.md) | Product spec, personas, flows, non-functional requirements, risks |
| [`docs/02-architecture.md`](docs/02-architecture.md) | Clean Architecture layers, module boundaries, backend + mobile structure |
| [`docs/03-database-schema.md`](docs/03-database-schema.md) | Full Postgres schema, pgvector usage, RLS, indexing, retention |
| [`docs/04-api-design.md`](docs/04-api-design.md) | REST/SSE contract, versioning, error envelope, auth, rate limits |
| [`docs/05-design-system.md`](docs/05-design-system.md) | Tokens, typography, spacing, motion, components, screen wireframes |
| [`docs/06-ai-pipeline.md`](docs/06-ai-pipeline.md) | Vision → analysis → guide → annotation pipeline; provider abstraction |
| [`docs/07-safety-policy.md`](docs/07-safety-policy.md) | Safety classification rules, red lines, confidence gating |
| [`docs/08-privacy-compliance.md`](docs/08-privacy-compliance.md) | GDPR/CCPA, App Store + Play requirements, data lifecycle, consent |
| [`docs/09-roadmap.md`](docs/09-roadmap.md) | Phased delivery plan with exit criteria |
| [`docs/10-progress-log.md`](docs/10-progress-log.md) | Chronological build log — **update on every change** |

## Quick start

### Backend

```bash
cd backend
cp .env.example .env                 # fill in secrets; never commit .env
docker compose up -d                 # Postgres 16 + pgvector, Redis 7
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload         # http://localhost:8000/docs
```

Tests:

```bash
cd backend
pytest                               # unit + integration
ruff check . && ruff format --check . && mypy app
```

The service runs without any AI provider key: `AI_PROVIDER=fake` uses
deterministic in-process fakes, which is also what CI uses. Nothing in the test
suite makes a network call.

### Mobile

Requires Flutter 3.24+ (stable).

```bash
cd mobile
flutter pub get
dart run build_runner build --delete-conflicting-outputs
flutter run --dart-define=API_BASE_URL=http://localhost:8000
```

## Architecture at a glance

```
┌──────────────────────────────────────────────────────────────┐
│  Flutter app (iOS / Android)                                 │
│  presentation → application (Riverpod) → domain → data       │
└───────────────┬──────────────────────────────────────────────┘
                │ HTTPS / JSON  •  SSE for streaming replies
┌───────────────▼──────────────────────────────────────────────┐
│  FastAPI (api/v1)                                            │
│    ├─ features/*  application services + domain entities     │
│    └─ infrastructure/*  adapters behind domain ports          │
├──────────────────────────────────────────────────────────────┤
│ Postgres + pgvector │ Redis │ Object storage │ Celery workers │
├──────────────────────────────────────────────────────────────┤
│ AI provider gateway → OpenAI │ Anthropic │ Gemini │ fake      │
└──────────────────────────────────────────────────────────────┘
```

Key architectural commitments, all of which exist to keep the roadmap in
[`docs/09-roadmap.md`](docs/09-roadmap.md) reachable without a rewrite:

- **Ports and adapters.** Domain code depends on protocols
  (`VisionPort`, `GuideGeneratorPort`, `ImageEditPort`, `StoragePort`,
  `KnowledgePort`). No feature imports an SDK. Swapping OpenAI for Anthropic is
  a configuration change plus one adapter file.
- **Feature-first modules.** Each feature owns its domain, application,
  and data code. Cross-feature calls go through application services, never
  through another feature's repository.
- **Analysis is a first-class entity, not a chat side effect.** A
  `Analysis` row with structured, versioned output is what guides are built
  from — which is what makes AR, cost estimation and shopping lists additive
  later rather than a refactor.
- **Every AI output is validated against a schema before it reaches the user.**
  Unvalidatable output is an error, not content.

## Engineering standards

- **Typing:** `mypy --strict` on backend `app/`; no `Any` at module boundaries.
  Dart with `strict-casts`/`strict-raw-types`.
- **Tests:** every application service has unit tests with faked ports; every
  API route has an integration test. Safety-classification and schema-validation
  logic requires tests for the failure paths, not just the happy path.
- **Commits:** Conventional Commits (`feat:`, `fix:`, `docs:`, `chore:`,
  `refactor:`, `test:`). One logical change per commit.
- **No secrets in the repo.** `.env` is git-ignored; `.env.example` documents
  every variable with a safe placeholder.
- **Errors:** a single error envelope (see `docs/04`), machine-readable codes,
  no stack traces or provider messages leaked to clients.

## Environment variables

Authoritative list lives in `backend/.env.example`. Summary:

| Variable | Purpose |
| --- | --- |
| `ENVIRONMENT` | `local` \| `staging` \| `production` — gates docs, debug, cookie flags |
| `SECRET_KEY` | JWT signing key (rotate per environment) |
| `DATABASE_URL` | Postgres DSN (asyncpg) |
| `REDIS_URL` | Cache + rate limiting + Celery broker |
| `AI_PROVIDER` | `fake` \| `openai` \| `anthropic` \| `gemini` |
| `OPENAI_API_KEY` etc. | Provider credentials — server-side only, never shipped to the app |
| `STORAGE_BACKEND` | `local` \| `supabase` \| `s3` |
| `SENTRY_DSN` | Error monitoring (optional) |

The mobile app never holds an AI provider key. All model calls are
server-side; the app authenticates with a short-lived JWT.

## Roadmap

| Phase | Scope | Status |
| --- | --- | --- |
| 1 | Architecture, schema, API design, design system, roadmap | ✅ Done |
| 2 | Auth, backend skeleton, home/camera/chat/project flows, AI integration | 🚧 In progress |
| 3 | Guide generation, vision pipeline, image annotation, history, settings | ⬜ Planned |
| 4 | Hardening, security, performance, CI/CD, store readiness | ⬜ Planned |

Detail and exit criteria per phase: [`docs/09-roadmap.md`](docs/09-roadmap.md).

## Progress log

The full log is [`docs/10-progress-log.md`](docs/10-progress-log.md), which is
updated in the same commit as the work it describes. Read it first when
resuming development.

## License

Proprietary. All rights reserved.
