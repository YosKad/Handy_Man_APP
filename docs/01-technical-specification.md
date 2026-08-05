# 01 — Technical Specification

Version 1.0 · Owner: Architecture · Status: approved for Phase 2

---

## 1. Problem statement

A person standing in front of a wall with a TV in a box has a narrow, concrete
question — *what anchor do I use in this wall, and will this mount fit this TV?*
The internet answers a general version of that question. The gap costs people
time, money, damaged walls, and occasionally injury.

HandyAI closes the gap by making the user's own situation the input. The product
thesis is that a multimodal model plus a disciplined knowledge and safety layer
can produce guidance that is *more* useful than a generic manual, precisely
because it is narrower.

## 2. Scope

### 2.1 In scope for v1 (production release)

- Three workflows: Install, Repair, Advise-before-buying.
- Multimodal intake: photo (required), text, voice (transcribed), short video
  (sampled to frames).
- Vision analysis producing a structured, confidence-scored `Analysis`.
- Clarification loop: the assistant asks only for what analysis could not
  determine, bounded to a small number of questions.
- Guide generation: tools, materials, safety checks, preparation, duration,
  difficulty, ordered steps, per-step verification, completion checklist,
  common mistakes.
- Per-step visual guidance by annotating the user's own photos.
- In-project conversational assistant with photo-based validation
  ("is this the right screw?").
- Safety classification (green / yellow / red) with hard professional-referral
  behaviour on red.
- Project persistence, step progress, history.
- Accounts, consent, data export, account deletion.

### 2.2 Explicitly out of scope for v1 (architecturally prepared)

AR overlays, live video assistance, real-time voice conversation, marketplace,
community guides, affiliate/retailer integrations, IoT, wearables, offline
authoring. Each appears in [`09-roadmap.md`](09-roadmap.md) with the extension
point it will use.

## 3. Users

| Persona | Situation | What they need | Failure they fear |
| --- | --- | --- | --- |
| **Cautious first-timer** | Just bought a flat-pack or a TV mount; has a drill, little confidence | Reassurance, exact hardware names, "am I doing this right?" checks | Ruining the wall or the product |
| **Competent DIYer** | Has done this before; wants speed | Compatibility facts, correct torque/anchor, no hand-holding | Wasting a trip to the store |
| **Renter** | Cannot make large holes; may need reversible solutions | Alternatives, damage assessment, landlord-safe options | Losing a deposit |
| **Pro (post-v1)** | Uses it to quote and document jobs | Fast capture, material lists, cost estimation | Looking unprofessional |

## 4. Core user flows

### 4.1 Install (primary flow)

```
Home → choose "Install"
  → Capture: guided multi-shot capture
      required : the product, the mounting location
      prompted : mount/bracket, hardware bag, existing fixings, wider room shot
  → Upload (background, compressed, resumable)
  → Analysis job
      identify product + model + specs, mount + VESA, wall material,
      obstacles, missing parts, measurements, per-field confidence
  → Confidence gate
      any critical field below threshold → request specific extra photo
        (never a guess on a safety-critical field)
  → Clarification (≤3 questions, each with a reason shown to the user)
  → Safety classification  → red? show referral, offer guide as read-only
  → Guide generation (schema-validated)
  → Annotation job per step (arrows/labels on the user's photos)
  → Project screen: timeline, step-by-step, per-step photo validation
  → Completion checklist → project archived to History
```

### 4.2 Repair

Same skeleton; analysis targets a *fault* rather than a product pairing, and the
generated artefact is a diagnosis (ranked causes with confidence) followed by a
repair guide for the selected cause.

### 4.3 Advise before buying

No installation site required. Intake is a product intent plus constraints
(wall type, viewing distance, room dimensions, movement preference, cable
management, budget). Output is a recommendation set: compatible product classes,
required accessories, installation requirements, warnings, and an honest
installation-difficulty forecast. Recommendations are specified as **product
classes and required attributes**, not SKUs — retailer integrations later attach
SKUs to the same structure without changing the domain model.

## 5. Functional requirements

| ID | Requirement |
| --- | --- |
| F-1 | User can create a project from 1–8 images plus optional text/voice/video. |
| F-2 | System extracts a structured analysis with a per-field confidence score. |
| F-3 | System requests additional imagery when any safety-critical field is below its confidence threshold. |
| F-4 | System asks at most 3 clarifying questions per project before generating a guide, each with a stated reason. |
| F-5 | Every guide is schema-valid before persistence; invalid model output is retried once, then failed with a user-visible, non-technical message. |
| F-6 | Every guide carries a safety classification and, on red, a professional-referral notice that cannot be dismissed away. |
| F-7 | Steps can be annotated on user images; annotation failure degrades to text-only steps, never blocks the guide. |
| F-8 | User can chat within a project, attach new photos, and receive answers grounded in the project's analysis and guide. |
| F-9 | Step completion is persisted and resumable across devices and app restarts. |
| F-10 | User can export all their data and delete their account, both self-service. |
| F-11 | All AI outputs record provider, model, prompt version, and cost for audit and regression testing. |

## 6. Non-functional requirements

| Area | Target |
| --- | --- |
| Cold start | < 2 s to interactive on a 2021 mid-range device |
| Capture → analysis result | p50 < 15 s, p95 < 40 s; progress is always visible |
| Guide generation | p50 < 25 s, streamed section-by-section so the user sees progress |
| API latency (non-AI) | p95 < 200 ms |
| Availability | 99.5% for the API; AI provider outage degrades to a queued job, not a 500 |
| Image upload | Client-side compression to ≤ 2048 px long edge, WebP/JPEG q80; background + resumable |
| Offline | Read access to all previously fetched projects and guides |
| Accessibility | WCAG 2.2 AA equivalent; full VoiceOver/TalkBack labels, dynamic type to 200%, contrast ≥ 4.5:1 |
| Localisation | English at launch; all strings externalised, RTL-safe layouts from day one |

## 7. Architectural risks and mitigations

This section is deliberately blunt; these are the things most likely to break
the product.

| # | Risk | Impact | Mitigation |
| --- | --- | --- | --- |
| R-1 | **Model hallucinates a safety-critical fact** (wrong anchor for drywall, understated TV weight) | Physical injury, property damage, legal exposure | Deterministic rules layer owns anchor/weight/load decisions; the model proposes, `features/safety` validates against a hardcoded compatibility table; unresolvable → yellow/red, never green |
| R-2 | **Vision misidentifies the product** and everything downstream is confidently wrong | Broken guide, lost trust | Per-field confidence with thresholds; user confirms the identified product/model before guide generation; identification is a visible, editable fact, not hidden state |
| R-3 | **Provider lock-in** | Cost and capability trap | Ports/adapters from the first commit; no SDK import outside `infrastructure/ai/*`; a `fake` adapter keeps the whole system testable without any provider |
| R-4 | **Unbounded AI cost per user** | Margin collapse | Per-user quota + per-request cost ceiling enforced before the call; every call records tokens and cost; aggressive caching of analysis by perceptual image hash |
| R-5 | **Image annotation quality** (generative editing distorts the user's photo) | Actively misleading visuals | Prefer deterministic overlay drawing (arrows/boxes/labels composited from model-returned coordinates) over generative editing; generative editing is opt-in per step and clearly marked |
| R-6 | **Long-running jobs over HTTP** | Timeouts, spinner-and-pray UX | Job model: POST creates a job, client subscribes via SSE/polling; jobs survive app restart |
| R-7 | **Privacy of home imagery** | Regulatory and reputational | Explicit per-upload consent; no training on user images by default; signed, expiring URLs; documented retention with self-service deletion |
| R-8 | **Schema drift between app and API** | Runtime breakage in the field | Single OpenAPI source of truth, generated Dart models, contract tests in CI, additive-only versioning within `v1` |
| R-9 | **Prompt-injection via images/text** (a photo containing "ignore previous instructions") | Guide manipulation | Model output is data, never instructions; strict output schemas; no tool execution driven by model text; system prompts pinned and versioned |
| R-10 | **Store rejection** (AI content, camera justification, account deletion) | Launch delay | Compliance requirements tracked as build tasks in [`08-privacy-compliance.md`](08-privacy-compliance.md), not as a pre-launch scramble |

## 8. Proposed improvements to the original brief

Recorded because they change the design, and future contributors should know
they were deliberate.

1. **Deterministic safety rules over model judgement.** The brief asks the AI
   not to hallucinate safety-critical instructions. Prompting is insufficient.
   Load-bearing decisions (anchor type for wall material and load, weight
   limits, electrical/gas/structural red lines) live in a versioned rules table
   with citations; the model's role is extraction and phrasing, not adjudication.
2. **Annotate deterministically, generate rarely.** Ask the vision model for
   *coordinates and labels*, then draw the overlay ourselves. This is cheaper,
   faster, reproducible, and cannot invent hardware that isn't in the photo.
3. **Analysis and Guide as versioned entities with immutable snapshots.** A
   guide the user is halfway through must not silently change because a prompt
   was updated. Regeneration creates a new revision; the in-progress one is
   pinned.
4. **Jobs, not long requests.** Every AI operation is an addressable job with
   status, cost, and provider metadata. This is what makes retries, audits,
   cost control, and regression testing possible.
5. **Product classes before SKUs.** Recommendations are attribute-based. This
   keeps affiliate/retailer integration a later adapter rather than a data-model
   migration, and keeps advice honest and retailer-neutral.
6. **"Confirm what I saw" step in the UI.** Cheap, and it converts the single
   worst failure mode (silent misidentification) into a five-second correction.
7. **A `fake` AI provider as a first-class citizen.** Not a test double bolted
   on later — it is the default in CI and local dev, which keeps the test suite
   fast, free, deterministic, and offline.

## 9. Success metrics

| Metric | Target |
| --- | --- |
| Capture → guide completion rate | > 70% |
| Guides rated "accurate for my situation" | > 85% |
| Projects completed (all steps checked) | > 45% |
| Safety-critical corrections reported by users | 0 tolerated; each is a P0 incident |
| AI cost per completed guide | < $0.35 |
| Crash-free sessions | > 99.5% |

## 10. Glossary

- **Analysis** — structured, versioned, confidence-scored interpretation of a
  project's media.
- **Guide** — schema-valid, safety-classified, step-structured instruction set
  derived from an Analysis.
- **Job** — an addressable async unit of AI work with status and cost metadata.
- **Port** — a protocol the domain depends on; **Adapter** — an implementation.
- **Safety class** — green (safe DIY) / yellow (proceed carefully) / red
  (professional recommended).
