# 10 — Progress Log

**This file is the handoff document.** Read it first when resuming development on
any machine or platform. It is updated in the same commit as the work it
describes.

Format per entry: what was built, decisions taken, what is verified, what is
next, and how to pick it up.

---

## 2026-08-05 — Entry 1: Phase 1 complete (architecture & specification)

### Built

`README.md` as the master document, plus the full specification set in `docs/`:

| File | What it settles |
| --- | --- |
| `01-technical-specification.md` | Scope, personas, the three flows in detail, functional + non-functional requirements, 10 architectural risks with mitigations, 7 deliberate deviations from the original brief, success metrics |
| `02-architecture.md` | Clean Architecture layering, feature module list with ownership, the port contracts, AI gateway design, job model, mobile architecture, extension points for every roadmap item |
| `03-database-schema.md` | Every table with columns and indexes, pgvector setup, RLS policies, retention matrix, migration policy |
| `04-api-design.md` | Full REST + SSE contract, error envelope with code table, auth flows, rate limits, contract-test rules |
| `05-design-system.md` | Colour/type/spacing/motion tokens, 15 components, 7 screen wireframes, accessibility requirements |
| `06-ai-pipeline.md` | The 9-stage pipeline, vision field schema, confidence thresholds, prompt invariants, provider abstraction, prompt versioning, cost controls |
| `07-safety-policy.md` | Classification behaviour, hard red lines, escalation table, never-hallucinate rules, incident process, test requirements |
| `08-privacy-compliance.md` | Data inventory, consent model, user rights, Apple + Play requirement tables, security controls, pre-launch checklist |
| `09-roadmap.md` | Four phases with exit criteria, then the post-v1 roadmap mapped to extension points |

### Key decisions (and why)

1. **Deterministic safety layer, not prompt-based.** Load-bearing decisions
   (anchor choice, weight limits, red lines) are resolved by a versioned
   `safety_rules` table and code in `features/safety`. The model extracts and
   phrases; it does not adjudicate. Prompting alone is not a safety control.
2. **Annotate deterministically.** The vision model returns coordinates and
   labels; the server draws the overlay with Pillow. ~100× cheaper than
   generative editing, reproducible, and it cannot invent hardware that isn't in
   the photo. Generative illustration stays behind a flag and is always labelled.
3. **Jobs, not long requests.** Every AI operation is a persisted job with
   status, cost, provider, and prompt version. Enables SSE progress, retries,
   audit, cost control, and regression testing.
4. **`Analysis` with per-field findings and confidence.** One row per extracted
   fact makes the confidence gate, the "confirm what I saw" UI, and user
   corrections possible — and turns silent misidentification into a five-second
   fix.
5. **Immutable guide revisions.** A guide the user has started cannot change
   under them; regeneration creates a revision and the pin only moves on opt-in.
6. **`fake` AI provider as a first-class adapter.** Default in CI and local dev,
   so the entire pipeline is testable offline, deterministically, at zero cost.
7. **Product classes, not SKUs.** Recommendations are attribute-specified, so
   retailer/affiliate integration is a later adapter rather than a data-model
   migration — and advice stays retailer-neutral.

### Verified

Documentation only; nothing executable in this entry.

### Environment note

Flutter SDK is **not** installed in the container these commits were authored in.
Backend (Python 3.11, Postgres 16, Redis 7, Docker) is fully runnable and is
being verified as it lands. Mobile code is written to production standard and
must be compiled by a developer machine or CI with Flutter 3.24+.

### Next

Phase 2 backend foundations — scaffold, `core`, domain + ports, AI gateway with
the `fake` adapter, and the safety classifier with its reference data.

---

## 2026-08-05 — Entry 2: Phase 2 backend core (scaffold, core, domain, gateway, safety, prompts)

### Built (all verified — see "Verified" below)

**Scaffold** — `backend/pyproject.toml` (ruff + mypy strict + pytest +
coverage config in one place), `docker-compose.yml` (Postgres 16 with pgvector,
Redis 7), `.env.example` documenting every variable, `Dockerfile`,
`Makefile`.

**`app/core/`**

- `config.py` — `pydantic-settings` typed settings with an `Environment` enum,
  computed properties (`is_production`, `docs_url`), per-provider key
  validation, and a fail-fast check that production never runs with the
  development secret or the `fake` provider.
- `errors.py` — `AppError` hierarchy carrying `code`, HTTP status, user message,
  `details`, and `retryable`, mapping 1:1 onto the envelope in `docs/04` §2.
  Provider and internal detail never reach the client.
- `logging.py` — structured JSON logging, request-id contextvar, and a redaction
  filter for tokens, prompts containing user content, and image bytes.
- `security.py` — Argon2id hashing, access/refresh JWT issue and verify, refresh
  family ids for reuse detection.
- `ratelimit.py` — Redis token bucket with the buckets from `docs/04` §10, and an
  in-memory implementation for tests.
- `container.py` — the single composition root: builds adapters, exposes ports.

**Domain + ports** for `auth`, `media`, `vision`, `guides`, `projects`, `chat`,
`safety`, `knowledge` — pure dataclasses/enums and `typing.Protocol` ports, zero
framework imports.

**AI gateway** (`infrastructure/ai/`) — capability-based adapter selection with
an ordered fallback list, timeout, jittered retry, per-request cost ceiling,
schema validation with one repair attempt, and one `ai_calls` ledger row per
attempt. `FakeAdapter` produces deterministic seeded fixtures for vision, guide
generation, chat streaming, and embeddings.

**Safety classifier** (`features/safety/domain/rules.py`) — 18 deterministic
rules implementing `docs/07`: the seven hard red lines, the load/material
comparison with the 1.25 safety factor applied in code, unknown-material and
unknown-weight escalation, mount under-rating, missing load-bearing hardware,
services-in-wall and tile/masonry cautions, knowledge-coverage and
uncertain-field floors, rental and minor-account rules. Reference data for 11
wall materials and 12 fasteners (with conservative, cited pull-out figures)
lives in `features/knowledge/reference/materials.py`.

**Prompts and schemas** — six versioned prompt files
(`infrastructure/ai/prompts/*.v1.md`) with a content-hashed registry, plus strict
JSON schemas (`infrastructure/ai/schemas.py`) generated from the domain
vocabulary so the schema cannot drift from the `Field` enum.

**OpenAI adapter** (`infrastructure/ai/adapters/openai_adapter.py`) — structured
outputs, image inputs, real token-based cost accounting, and pure module-level
parsers that raise `ProviderContractError` on any contract violation.

**FastAPI app** (`app/main.py`, `app/api/v1/`) — app factory, lifespan wiring the
container, request-id correlation middleware, security headers, the error
envelope applied to *every* failure path, health probes, and `/v1/meta`.

**Tooling** — `Makefile`, multi-stage `Dockerfile` (non-root, healthcheck),
`scripts/check_architecture.py`, and `.github/workflows/backend.yml`.

### Verified

Run in this container against Python 3.11.15:

| Check | Result |
| --- | --- |
| `pytest` | **211 passed**, 0 failed; 94% statement coverage of `app/` |
| `ruff check .` | clean |
| `ruff format --check .` | clean |
| `mypy app tests` (strict) | clean, 81 files |
| `scripts/check_architecture.py` | ok — no provider SDK outside `infrastructure/ai/adapters/`, domain layers pure, no relative imports |
| `uvicorn app.main:app` | boots; `/health/live`, `/health/ready`, `/v1/meta` serve correctly; JSON logs emit with correlation ids; 404 returns the error envelope |

The suite makes no network calls and needs no database, because `AI_PROVIDER=fake`
covers the whole pipeline.

**Not verified here:** the Docker image build (no Docker daemon in this
container) and the OpenAI adapter against the live API (CI runs the fake
adapter). Both are called out in the code and must be exercised before staging.

### Bug found and fixed during verification

Booting the app revealed that unmatched routes returned Starlette's
`{"detail": "Not Found"}` rather than the documented envelope — two error shapes
for the client to handle. Added a `StarletteHTTPException` handler mapping
routing failures onto the error vocabulary, with tests for 404, 405 and query
validation. This is the kind of gap only running the thing finds.

### Next

1. SQLAlchemy models + Alembic initial migration + reference-data seed migration.
2. Auth endpoints (register/login/refresh with family rotation and reuse detection).
3. Media upload flow with EXIF stripping and perceptual hashing.
4. Project create → analyze → job → analysis endpoints, then chat with SSE.
5. Flutter scaffold: tokens, themes, router, design-system components.
