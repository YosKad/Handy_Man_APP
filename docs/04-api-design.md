# 04 — API Design

Base URL: `https://api.handyai.app` · Current version: `/v1` · Content type:
`application/json; charset=utf-8` · Transport: HTTPS only (HSTS, TLS 1.2+)

OpenAPI is generated from the code and served at `/openapi.json`
(`/docs` only when `ENVIRONMENT != production`). The generated schema is the
single source of truth for mobile DTO generation and contract tests.

---

## 1. Conventions

- **Versioning.** `/v1` is additive-only: new optional fields and new endpoints
  are allowed; removals or type changes require `/v2`. Clients must ignore
  unknown fields.
- **IDs.** UUID strings. Never sequential, never guessable.
- **Timestamps.** RFC 3339 UTC with `Z`.
- **Casing.** `snake_case` in JSON, matching the backend and the generated Dart
  models.
- **Pagination.** Cursor-based: `?limit=20&cursor=<opaque>`; response carries
  `next_cursor` (null at the end). No offset pagination.
- **Idempotency.** All POSTs that create work accept `Idempotency-Key`; replay
  returns the original result.
- **Correlation.** `X-Request-ID` echoed on every response and present in every
  log line, job, and AI call for that request.

## 2. Error envelope

Every non-2xx response, without exception:

```json
{
  "error": {
    "code": "confidence_too_low",
    "message": "We need one more photo of the wall to be sure.",
    "details": { "missing_fields": ["wall.material"] },
    "request_id": "01J8Z…",
    "retryable": true
  }
}
```

- `code` is a stable machine-readable enum; the app switches on it and never
  parses `message`.
- `message` is user-presentable, plain, non-technical, localisable.
- Provider errors, stack traces, SQL, and internal hostnames are never included.

| HTTP | Codes |
| --- | --- |
| 400 | `validation_error`, `unsupported_media_type`, `image_too_large` |
| 401 | `unauthenticated`, `token_expired` |
| 403 | `forbidden`, `consent_required`, `entitlement_required` |
| 404 | `not_found` |
| 409 | `conflict`, `guide_immutable` |
| 422 | `confidence_too_low`, `clarification_required`, `safety_blocked` |
| 429 | `rate_limited` (with `Retry-After`) |
| 500 | `internal_error` |
| 502/503 | `provider_unavailable`, `provider_contract_error` |

## 3. Authentication

JWT bearer. Access token 15 min, refresh token 30 days, rotated on use with
reuse detection (a replayed refresh token revokes the family).

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/v1/auth/register` | email + password; returns token pair |
| POST | `/v1/auth/login` | email + password |
| POST | `/v1/auth/social` | Apple / Google identity token exchange |
| POST | `/v1/auth/refresh` | rotate refresh token |
| POST | `/v1/auth/logout` | revoke the presented refresh family |
| GET | `/v1/auth/me` | current user + entitlements + consent state |
| POST | `/v1/auth/password/reset-request` | email a reset link |
| POST | `/v1/auth/password/reset` | complete reset |

Tokens are stored in the OS keychain/keystore on the client — never in
`SharedPreferences`, never in plain files.

## 4. Media

```
POST /v1/media/uploads          → { upload_id, upload_url, storage_key, expires_at }
PUT  <upload_url>               → direct upload to object storage (no API hop)
POST /v1/media/uploads/{id}/complete
     { role: "wall", project_id?, consent_version }
     → { media: MediaAsset }
GET  /v1/media/{id}             → MediaAsset (signed_url, 10 min TTL)
DELETE /v1/media/{id}
```

Rules enforced server-side on completion:

- allowed types: `image/jpeg`, `image/png`, `image/webp`, `image/heic`,
  `video/mp4` (≤ 30 s), `audio/m4a` (≤ 120 s)
- max 12 MB per image, 60 MB per video
- EXIF stripped, dimensions and `phash` computed
- `consent_version` must match an active granted `image_processing` consent, or
  `403 consent_required`

## 5. Projects (the orchestrating resource)

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/v1/projects` | create draft: `{ workflow, intent_text?, site_id?, media_ids[] }` |
| GET | `/v1/projects` | list (cursor, filter by `status`, `workflow`) |
| GET | `/v1/projects/{id}` | full project: analysis summary, active guide, progress |
| PATCH | `/v1/projects/{id}` | rename, change site, abandon |
| DELETE | `/v1/projects/{id}` | delete project and its media |
| POST | `/v1/projects/{id}/media` | attach more media |
| POST | `/v1/projects/{id}/analyze` | 202 → job |
| GET | `/v1/projects/{id}/analysis` | latest analysis with findings |
| PATCH | `/v1/projects/{id}/analysis/findings` | user corrections: `[{ field, value }]` |
| POST | `/v1/projects/{id}/clarifications` | answer pending questions |
| POST | `/v1/projects/{id}/guide` | 202 → guide generation job |
| GET | `/v1/projects/{id}/guide` | active guide with steps |
| POST | `/v1/projects/{id}/guide/regenerate` | new revision (does not move the pin unless `activate: true`) |
| POST | `/v1/projects/{id}/steps/{step_id}/progress` | `{ state, evidence_media_id? }` |
| POST | `/v1/projects/{id}/steps/{step_id}/validate` | 202 → job validating an evidence photo |
| POST | `/v1/projects/{id}/complete` | finish and archive |

### 5.1 Project state as seen by the client

```json
{
  "id": "…",
  "workflow": "install",
  "title": "Mount 55\" TV on drywall",
  "status": "needs_input",
  "safety_class": "yellow",
  "analysis": {
    "overall_confidence": 0.72,
    "confirmed_by_user": false,
    "findings": [
      { "field": "product.model", "value": "Samsung UN55TU8000", "confidence": 0.91,
        "is_safety_critical": false, "evidence_media_id": "…" },
      { "field": "product.weight_kg", "value": 15.4, "confidence": 0.62,
        "is_safety_critical": true, "evidence_media_id": null }
    ]
  },
  "pending_requests": [
    { "kind": "photo", "field": "wall.material",
      "prompt": "A close-up of the wall where the mount will go",
      "reason": "The anchor depends on whether this is drywall or plaster." }
  ],
  "clarifications": [
    { "id": "…", "question": "Is there a power outlet behind the TV?",
      "reason": "It changes the cable-management step.",
      "options": ["Yes", "No", "Not sure"] }
  ]
}
```

`pending_requests` and `clarifications` are how the confidence gate reaches the
UI. The client renders them as first-class cards, not as chat messages, and each
carries the *reason* it is being asked — the user always knows why.

## 6. Jobs

```
GET /v1/jobs/{id}
    → { id, kind, status, progress, result?, error?, cost_usd? }
GET /v1/jobs/{id}/events        (text/event-stream)
    event: progress  data: { "progress": 45, "stage": "identifying_mount" }
    event: done      data: { "result": { … } }
    event: error     data: { "code": "provider_unavailable", "retryable": true }
```

SSE with a 15 s heartbeat comment to survive proxies. Clients fall back to
polling `GET /v1/jobs/{id}` every 2 s with backoff if the stream drops.

## 7. Chat

```
POST /v1/conversations                { project_id? } → Conversation
GET  /v1/conversations                list
GET  /v1/conversations/{id}/messages  cursor-paginated history
POST /v1/conversations/{id}/messages  { content, media_ids[] } → 202 { message_id, stream_url }
GET  /v1/conversations/{id}/stream?after=<message_id>   (text/event-stream)
     event: delta      data: { "text": "That screw is …" }
     event: reference  data: { "kind": "guide_step", "id": "…" }
     event: done       data: { "message_id": "…", "safety_class": "green" }
```

The assistant is always grounded: the server assembles context from the
project's analysis, active guide, current step, and retrieved knowledge chunks.
The client never sends a system prompt, and the server ignores any attempt to.

## 8. Advice workflow

```
POST /v1/advice/sessions        { intent_text, media_ids[]? }  → session + first questions
POST /v1/advice/sessions/{id}/answers   { answers: [{question_id, value}] }
GET  /v1/advice/sessions/{id}/recommendation
     → { product_classes: [{ key, required_attributes, rationale, warnings }],
         accessories: […], installation: { difficulty, safety_class, notes },
         next_step: { create_project: true } }
```

Recommendations are attribute-based, never SKU-based, in v1 — see
[`01`](01-technical-specification.md) §8.5.

## 9. Account, privacy, and settings

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/v1/account/privacy` | consent state, retention settings, data summary |
| POST | `/v1/account/consents` | grant/revoke a consent kind + version |
| POST | `/v1/account/export` | 202 → export job |
| GET | `/v1/account/export/{id}` | status + signed download URL |
| POST | `/v1/account/delete` | start 30-day deletion; returns `purge_at` |
| POST | `/v1/account/delete/cancel` | cancel during grace period |
| GET/PATCH | `/v1/account/preferences` | locale, units, notifications, theme |
| POST | `/v1/account/devices` | register FCM token |

Account deletion is reachable in-app in two taps from Profile — an App Store
and Play requirement, not a nice-to-have.

## 10. Rate limits

Redis token bucket, per user (and per IP for unauthenticated routes).

| Bucket | Limit |
| --- | --- |
| auth (login/register/reset) | 10 / 15 min / IP |
| media uploads | 60 / hour / user |
| analysis jobs | 20 / hour / user |
| guide generation | 30 / hour / user |
| chat messages | 60 / hour / user |
| reads | 600 / 5 min / user |

Exceeding returns `429` with `Retry-After` and a friendly message. Free-tier
quota is enforced separately by `billing` and returns
`403 entitlement_required` — two different situations, two different codes, so
the app can say the right thing.

## 11. Health and ops

- `GET /health/live` — process is up (no dependency checks).
- `GET /health/ready` — Postgres, Redis, storage, and the configured AI provider
  reachable; returns per-dependency status.
- `GET /metrics` — Prometheus format; internal network only.

## 12. Security headers and CORS

`Strict-Transport-Security`, `X-Content-Type-Options: nosniff`,
`Referrer-Policy: no-referrer`, `Cache-Control: no-store` on authenticated
responses. CORS allows only known first-party web origins; the mobile app does
not need CORS and the wildcard origin is never used.

## 13. Contract testing

CI fails on any of:

- a route whose response model is not declared,
- a schema change that removes or retypes an existing `v1` field
  (checked by diffing `openapi.json` against the committed snapshot),
- a route missing an integration test,
- an error response that does not match the envelope schema.
