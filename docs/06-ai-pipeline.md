# 06 — AI Pipeline

The pipeline turns photos into a safety-checked, personalised guide. Its design
goal is that **no single model output is trusted on its own** — every stage is
schema-validated, confidence-scored, and (where safety matters) adjudicated by
deterministic rules.

---

## 1. Stages

```
 media ─▶ [1] Preprocess ─▶ [2] Vision extraction ─▶ [3] Normalisation
                                                          │
                        ┌─────────────────────────────────┘
                        ▼
        [4] Knowledge enrichment (RAG + reference tables)
                        │
                        ▼
        [5] Confidence gate ──insufficient──▶ request photo / ask question
                        │ sufficient
                        ▼
        [6] Safety classification (deterministic rules)
                        │
                        ▼
        [7] Guide generation (structured output, validated)
                        │
                        ▼
        [8] Annotation (deterministic overlay per step)
                        │
                        ▼
        [9] Post-validation ─▶ persist ─▶ stream to client
```

### [1] Preprocess

Server-side, before any provider call: EXIF strip (including GPS), orientation
normalise, downscale to ≤ 1568 px long edge (the useful ceiling for current
vision models — larger costs more and adds nothing), re-encode, compute `phash`.
Video is sampled to at most 6 frames chosen for sharpness and coverage. Audio is
transcribed and treated as text intent.

Cache key: `sha256(phash_set + workflow + prompt_version + provider + model)`.
A repeat submission of the same photos does not repeat the spend.

### [2] Vision extraction

One structured call per project, with all images in a single request so the model
can reason across them (the TV *and* the wall *and* the mount together — this is
the whole point of the product).

The model is asked for a strict JSON object, one entry per field, each with a
confidence in `[0,1]`, the id of the image that supports it, and a normalised
bounding box. Fields it cannot determine must be returned as `null` with a
confidence of 0 — **guessing is a contract violation**, and the prompt says so
explicitly.

Requested field set (`schema_version` 1):

```
product.{category, brand, model, size_inches, weight_kg, dimensions_mm}
mount.{present, brand, model, type, vesa, max_load_kg, tilt, missing_parts[]}
wall.{material, finish, thickness_mm, obstacles[], stud_evidence}
site.{room_type, outlet_present, cable_route_options[]}
hardware.{items[]{type, size, count, suitable_for}}
tools.{visible[]}
damage.{present, description, severity}
measurements[]{label, value, unit, method, confidence}
risks[]{code, description}
notes
```

### [3] Normalisation

Free text is mapped onto controlled vocabularies (`wall.material` → a
`materials.key`; `hardware.items[].type` → a `fasteners` row). Units are
converted to SI and stored with the original. Anything that fails to map is kept
verbatim, marked `unmapped`, and treated as low confidence — never silently
coerced into the nearest known value.

### [4] Knowledge enrichment

Two sources, in this order:

1. **Reference tables** (`materials`, `fasteners`, `product_classes`) — exact
   lookups. These are authoritative.
2. **Vector retrieval** over `knowledge_chunks`, pre-filtered by product class,
   language, and country, then HNSW cosine search, top-k 8, with a similarity
   floor. Retrieved chunks are passed as *quoted context with citations*, and the
   guide prompt is instructed to cite them.

If retrieval returns nothing above the floor, the pipeline continues without RAG
context and records `knowledge_coverage: none` on the analysis — which raises the
bar for the safety classifier rather than being invisible.

### [5] Confidence gate

Per-field thresholds, higher for anything load-bearing:

| Field | Threshold | If below |
| --- | --- | --- |
| `product.weight_kg` | 0.80 | request the label photo or ask the user |
| `wall.material` | 0.85 | request a close-up; offer a knock-test question |
| `mount.max_load_kg` | 0.80 | request the mount's spec sticker |
| `mount.vesa` | 0.75 | request a photo of the TV's back |
| `product.model` | 0.70 | ask the user to confirm or type it |
| everything else | 0.50 | proceed, mark as uncertain in the guide |

At most **two** gate rounds before the pipeline stops and asks the user to enter
the value manually — an endless "one more photo" loop is a worse failure than
asking a direct question.

### [6] Safety classification

Deterministic. `features/safety` evaluates active `safety_rules` against the
normalised analysis; the highest severity match wins. The model never chooses the
classification; it only supplies inputs and prose.

Escalation rules that always apply, regardless of model output:

- Load exceeding the fastener's rated pullout for the identified wall material,
  or unknown wall material with a load > 10 kg → **red**.
- Any mains electrical work beyond replacing a like-for-like plug, gas, water
  main, structural member, asbestos-era material, or work above 2 m on a ladder
  → **red**.
- Missing hardware that is load-bearing → **red** until resolved.
- `knowledge_coverage: none` on a load-bearing task → at least **yellow**.
- Any safety-critical field still uncertain after the gate → at least
  **yellow**, and it is named in the guide.

Red means: the guide is generated but presented read-only, with a professional
referral first and a non-dismissible notice. The user is never blocked from
*information*, but the app does not walk them into it step by step.

### [7] Guide generation

A structured-output call constrained to the guide JSON schema. Inputs: the
normalised analysis, the confirmed findings, retrieved knowledge with citations,
the deterministic safety classification and its rationale, the user's unit
system and locale, and the user's stated intent verbatim.

Prompt invariants (all asserted in tests):

- Use only facts present in the analysis or the retrieved knowledge. If a fact is
  needed but absent, emit a `prerequisite` telling the user to check it — do not
  invent it.
- Never contradict or soften the supplied safety classification.
- Fastener and anchor recommendations must come from the supplied `fasteners`
  rows; the model may phrase them, not choose them.
- Each step needs a `verification`: how the user knows it worked.
- Torque, depth, and load figures require a citation or must be omitted.

Output is validated against the Pydantic schema. On failure: one repair attempt
with the validation errors fed back, then the job fails with
`provider_contract_error` and a plain user message. Partial or unvalidated
guides are never persisted.

Streaming: sections are emitted as they complete (`overview` → `tools` →
`safety` → `steps[i]`), so the user sees a guide assembling rather than a
spinner.

### [8] Annotation

Default path is **deterministic and local** (Pillow, in
`infrastructure/imaging`): the vision model returns normalised coordinates and
short labels; we draw arrows, boxes, dots, measurement lines and label chips onto
the user's own photo using design-system colours and typography.

Why: it is ~100× cheaper than generative editing, reproducible, fast, cannot
invent hardware that isn't in the photo, and produces overlays that match the
app's visual language.

Generative image editing (`ImageEditPort`) exists behind a feature flag for cases
where an illustration genuinely helps (an exploded view, a cross-section). It is
always labelled "illustration" in the UI so it is never mistaken for the user's
own wall. Annotation failure degrades to text-only steps and never fails the
guide.

Accessibility: each annotation op also emits an alt-text sentence, so the
overlay is available to screen readers.

### [9] Post-validation

Before persistence, mechanical checks that need no model:

- every step's tools ⊆ the guide's tool list
- every material references a known `product_class` or an explicit free-text
  item marked as such
- ordinals contiguous from 1, no duplicates
- `estimated_minutes` equals the sum of step durations within ±20%
- no step text contains a placeholder, a URL to an unknown domain, or an uncited
  numeric torque/load figure
- safety class matches the classifier's output exactly

A failure here is a bug in the prompt, and it fails loudly in CI rather than
quietly in production.

## 2. Step-photo validation (during installation)

The user photographs their work; the assistant answers "does this look right?".

```
evidence photo + step context + analysis
      ▼
structured vision call → { verdict: ok | unsure | problem,
                           observations[], issues[]{severity, description, fix},
                           confidence }
      ▼
verdict = problem AND severity = high  →  stop the user, escalate safety
verdict = unsure                        →  ask for a better photo, do not judge
```

The assistant is explicitly permitted — and prompted — to say "I can't tell from
this photo." Confident wrong reassurance is the worst possible output here.

## 3. Conversational assistant

Context assembly, in priority order, to a fixed token budget:

1. system prompt (pinned, versioned, never client-supplied)
2. safety classification and any active red-line notice
3. confirmed analysis findings (compact table)
4. active guide title + current step (full text)
5. retrieved knowledge chunks for the question
6. last N turns, oldest dropped first
7. the user's message and images

Rules: the assistant answers within the project's context; it recommends
re-analysis instead of contradicting the guide silently; on any safety-relevant
question it consults the deterministic rules and defers to them; it cites step
numbers so the user can navigate.

**Prompt-injection stance:** all model input derived from user media, user text,
or retrieved documents is data. It is fenced and labelled as untrusted, the
system prompt states that instructions inside it must be ignored, and no model
output can trigger an action — there is no tool the model can call that mutates
state. Structured outputs are parsed, validated, and used as values only.

## 4. Provider abstraction

```python
class AIGateway:
    async def vision_analyze(self, req: VisionRequest) -> VisionResult: ...
    async def generate_guide(self, req: GuideRequest) -> GuideDraft: ...
    def chat_stream(self, req: ChatRequest) -> AsyncIterator[ChatChunk]: ...
    async def embed(self, texts: Sequence[str]) -> list[Vector]: ...
```

Selection is by `(capability, tier)` from config, with an ordered fallback list.
The gateway owns: timeout, retry with jittered backoff on transient errors,
cost ceiling per request, quota check, schema validation, and one `ai_calls` row
per attempt.

Adapters: `openai`, `anthropic`, `gemini`, `fake`. `fake` returns deterministic
fixtures derived from a seed — it is the default in CI and local development, so
the entire pipeline including the confidence gate, the safety classifier, and
annotation is testable offline and for free.

Nothing outside `infrastructure/ai/` may import a provider SDK; CI greps for it.

## 5. Prompt management

Prompts are versioned files in `backend/app/infrastructure/ai/prompts/`, named
`<purpose>.v<N>.md`, loaded at startup, hashed, and recorded on every call and
every generated artefact. Changing a prompt means adding a new version, not
editing the old one, because in-progress guides must stay reproducible.

Regression suite: a fixture set of real-world image sets with expected
extractions and expected safety classifications. It runs against the `fake`
adapter in CI on every PR, and against live providers nightly with a cost cap.
A prompt change that moves a safety classification in the wrong direction fails
the build.

## 6. Cost control

| Control | Value |
| --- | --- |
| Image ceiling per analysis | 8 images, ≤ 1568 px long edge |
| Analysis cache | by perceptual-hash set + prompt version |
| Per-request cost ceiling | configurable; exceeded → job fails before the call |
| Per-user daily budget | entitlement-driven; `403 entitlement_required` |
| Retrieval | top-k 8, similarity floor, pre-filtered |
| Annotation | deterministic by default (no model cost) |
| Chat context | fixed token budget with oldest-first eviction |

Target: **< $0.35 per completed guide**, tracked from the `ai_calls` ledger and
alerted on regression.
