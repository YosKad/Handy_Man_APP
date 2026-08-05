# 09 — Roadmap

Four phases to a store-ready v1, then the extension roadmap. Each phase has exit
criteria; a phase is not "done" because the code exists — it is done when the
criteria pass.

---

## Phase 1 — Architecture & specification ✅

**Delivered:** product spec with risks and design amendments, Clean Architecture
definition for backend and mobile, full database schema, API contract, design
system with wireframes, AI pipeline design, safety policy, privacy/compliance
requirements, this roadmap, and the progress log.

**Exit criteria**

- [x] Every doc in `docs/` written and internally consistent
- [x] Architectural risks identified with mitigations (`01` §7)
- [x] Deviations from the original brief recorded with rationale (`01` §8)
- [x] Extension points named for every roadmap item (`02` §6)

## Phase 2 — Foundations 🚧

Backend skeleton and the mobile shell: everything except the AI depth.

**Backend**

- [x] Project scaffold: `pyproject.toml`, ruff/mypy/pytest config, docker-compose
- [x] `core`: typed settings, structured logging, error envelope, DI container,
      security primitives, rate limiting
- [x] Ports and domain entities for `auth`, `media`, `vision`, `guides`,
      `projects`, `safety`, `chat`
- [x] AI gateway with the `fake` adapter and the `ai_calls` ledger
- [x] Deterministic safety classifier with reference data
- [ ] SQLAlchemy models + Alembic initial migration + seed reference data
- [ ] Auth endpoints with JWT rotation and reuse detection
- [ ] Media upload flow with EXIF stripping and `phash`
- [ ] Project create → analyze → job → analysis endpoints
- [ ] Chat endpoints with SSE streaming
- [ ] Test suite green: unit + integration, `mypy --strict`, ruff clean

**Mobile**

- [ ] Flutter scaffold, design tokens, light/dark themes, GoRouter, Riverpod, Dio
- [ ] Design-system component library with golden tests
- [ ] Onboarding, auth, Home, Capture, Project, Chat, History, Profile shells
- [ ] Generated API client wired to the real backend

**Exit criteria**

- [ ] A user can register, photograph a subject, receive an analysis from the
      `fake` provider, and see a rendered guide, end to end on a device
- [ ] CI runs backend lint/type/test and Flutter analyze/test on every push
- [ ] No provider SDK imported outside `infrastructure/ai/`

## Phase 3 — Intelligence

The part that makes the product worth using.

- [ ] Real vision adapter with the full field schema and per-field confidence
- [ ] Normalisation onto controlled vocabularies
- [ ] Knowledge engine: ingestion, chunking, embeddings, pgvector retrieval with
      pre-filtering, citations
- [ ] Guide generation with structured output, streaming sections, and
      post-validation
- [ ] Deterministic annotation renderer + alt-text generation
- [ ] Clarification loop and confidence gate wired through to the UI
- [ ] Step-photo validation
- [ ] Grounded conversational assistant with context assembly and budget
- [ ] Advice-before-buying workflow
- [ ] History, search, settings, privacy dashboard, export, deletion
- [ ] Prompt regression suite with safety fixtures

**Exit criteria**

- [ ] 20-scenario internal evaluation set: ≥ 85% of guides judged accurate by a
      competent DIYer, **zero** unsafe recommendations
- [ ] Safety classification correct on 100% of the hard-red-line fixtures
- [ ] Median cost per completed guide < $0.35
- [ ] Annotation produces a usable overlay on ≥ 90% of the fixture photos

## Phase 4 — Production readiness

- [ ] Performance: cold start < 2 s, image pipeline profiled, list virtualisation,
      request coalescing
- [ ] Offline: cached reads, write outbox for step progress
- [ ] Observability: Sentry both sides, structured logs, metrics, cost and safety
      dashboards, alerting
- [ ] Security: pen test, dependency and container scanning, secret rotation
      drill, MASVS L1 review
- [ ] Privacy: retention jobs live and verified, export and deletion verified end
      to end
- [ ] Accessibility audit with VoiceOver and TalkBack on real devices
- [ ] Localisation infrastructure proven with a second locale, RTL verified
- [ ] Store readiness: metadata, screenshots, privacy labels, `PrivacyInfo`, Data
      Safety form, reviewer demo account
- [ ] CI/CD: staging on merge, TestFlight and Play internal track on tag,
      migrations gated, rollback documented
- [ ] Runbooks: provider outage, cost spike, safety incident, data-deletion
      request

**Exit criteria**

- [ ] Crash-free sessions > 99.5% across a 2-week internal beta
- [ ] p95 API latency < 200 ms for non-AI endpoints
- [ ] Both store submissions pass review
- [ ] Every item in `08` §11 checked

## Post-v1 roadmap

Ordered by value over effort, each mapped to the extension point that already
exists.

### Near term (v1.1 – v1.3)

| Feature | Extension point |
| --- | --- |
| Automatic shopping list from `materials[]` | `CatalogPort` over existing attribute-specified materials |
| Retailer integrations (Amazon, Home Depot, Lowe's, B&Q) | `CatalogPort` adapters + affiliate link decoration |
| Repair cost and material estimation | new application service over the existing analysis |
| Saved homes, rooms, and per-site defaults | `sites` table already in the schema |
| AI memory ("you have a stud finder") | `user_memory` + `MemoryPort` in prompt assembly |
| Subscriptions and AI credits | `entitlements` + `credit_ledger`, already enforced at the gateway |
| Guide sharing and PDF export | render from the persisted guide schema |
| Voice conversation | `ChatPort` streaming adapter + TTS |

### Medium term (v2)

| Feature | Extension point |
| --- | --- |
| Live video assistance | realtime `ChatPort` adapter with a frame source |
| AR overlays | `guide_steps.spatial_anchors`, already reserved |
| Tool detection and inventory | new vision purpose reusing the analysis pipeline |
| Animated and 3D instructions | additional media kinds on the existing step media slot |
| Professional mode | existing `role` + entitlement checks |
| Manufacturer manual ingestion at scale | `knowledge_documents` ingestion pipeline |
| Offline authoring | extend the existing write outbox |

### Long term (v3+)

Marketplace for professionals, community guides and ratings, IoT integrations,
smart-glasses and Vision Pro clients (all consume the same `/v1` API), Wear OS
and watchOS companions, enterprise plans.

## Cadence and quality gates

- Trunk-based development; short-lived branches; every PR reviewed.
- CI on every push: ruff, `mypy --strict`, pytest with coverage, `flutter
  analyze`, `flutter test`, OpenAPI contract diff, provider-SDK import check.
- Migrations reviewed for lock behaviour; forward-only.
- Every feature ships with its documentation update in the same PR. A PR that
  changes behaviour without touching `docs/` or the progress log is incomplete.
