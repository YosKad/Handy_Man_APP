# 07 — Safety Policy

**Safety outranks helpfulness.** Where the two conflict, the product is less
helpful. This is not negotiable and is not a prompt instruction — it is
implemented as deterministic code in `backend/app/features/safety/`.

---

## 1. Classification

| Class | Meaning | Product behaviour |
| --- | --- | --- |
| 🟢 **Green** | Ordinary DIY, tolerant of small mistakes | Full step-by-step, normal flow |
| 🟡 **Yellow** | Doable, but a mistake has real cost | Prominent cautions, mandatory pre-flight checklist, uncertainties named explicitly |
| 🔴 **Red** | Professional recommended | Guide is read-only and informational; referral shown first; non-dismissible notice; no step-by-step execution flow, no step validation |

The class is computed, not generated. The model supplies facts; `safety_rules`
decides. If the rules cannot decide, the answer is the more severe class.

## 2. Hard red lines (always red)

Independent of confidence, user insistence, or model opinion:

- Mains electrical work beyond a like-for-like plug or a switch-plate cover:
  consumer units, new circuits, rewiring, anything behind a live outlet.
- Gas: any appliance, pipe, fitting, or suspicion of a leak.
- Water mains, sewage, or pressurised heating systems.
- Structural members: load-bearing walls, joists, lintels, beams.
- Roof work, or any work requiring a ladder above ~2 m.
- Materials with an asbestos-era profile (textured coatings, older Artex-type
  ceilings, pre-1990 insulation boards) — stop, do not disturb, refer.
- Anything overhead and heavy: ceiling mounts, ceiling fans, projectors,
  suspended shelving above seating or a bed.
- Fire-safety or means-of-escape alterations.
- Weight-bearing installations where the wall material cannot be determined and
  the load exceeds 10 kg.
- Any child-safety-critical installation (anti-tip, window restrictors, stair
  gates) where hardware is missing or substituted.

Red-line copy is fixed, human-written, and reviewed — it is never model-generated.

## 3. Escalation rules (green → yellow → red)

Evaluated in order; the highest severity wins.

| Condition | Result |
| --- | --- |
| Load > fastener's rated pullout for the identified material | red |
| Load > 60% of rated pullout | yellow |
| `wall.material` unknown after the gate, load > 10 kg | red |
| `wall.material` unknown, load ≤ 10 kg | yellow |
| `product.weight_kg` unknown after the gate, wall mounting involved | red |
| Load-bearing hardware missing or substituted | red until resolved |
| Mount's `max_load_kg` < product weight × 1.25 safety factor | red |
| Drilling near a detected or plausible electrical/plumbing route | yellow + mandatory detector step |
| Tile or masonry drilling | yellow (technique-sensitive, hard to undo) |
| `knowledge_coverage: none` on a load-bearing task | yellow minimum |
| Any safety-critical field still uncertain | yellow minimum, uncertainty named in the guide |
| Renter site (`sites.kind = 'rental'`) with irreversible modification | yellow + reversible alternatives offered |
| Minor account or child-account signal | red for any tool-based task |

A safety factor of **1.25** is applied to all load comparisons, and it is applied
in code, not left to the model.

## 4. Never-hallucinate rules

Enforced by post-validation ([`06`](06-ai-pipeline.md) §9), not by hope:

1. No numeric torque, depth, load, or clearance figure without a citation from
   `fasteners`, `safety_rules`, or a retrieved manual chunk. Uncited numbers are
   stripped and the step is rewritten to say "check the manufacturer's figure".
2. No fastener or anchor recommendation that is not a row in `fasteners` matched
   to the identified material and load class.
3. No claim about a specific product's specification that did not come from the
   analysis or a retrieved document.
4. No invented part names, model numbers, or hardware not visible in the photos
   or listed in a manual.
5. "I don't know" is a valid and preferred output. The assistant is prompted for
   it and tested on it.

## 5. Confidence gating

A safety-critical field below its threshold cannot be assumed. The pipeline may:

1. request a specific additional photo (with a stated reason), at most twice; or
2. ask the user directly for the value; or
3. escalate the classification.

It may never proceed by inference on a safety-critical field. See
[`06`](06-ai-pipeline.md) §5 for thresholds.

## 6. What the user sees

- **Green:** a safety chip in the header; per-step notes where relevant.
- **Yellow:** a caution card above step 1 listing exactly what to be careful
  about and what is uncertain, plus a pre-flight checklist that must be
  acknowledged before step 1 expands.
- **Red:** a full-width notice before any content: what the risk is, why a
  professional is recommended, what to ask them, and a rough cost expectation.
  The guide below is readable but has no step-completion controls. The notice
  cannot be dismissed, and the language is direct without being alarmist.

Every safety statement is accompanied by its *reason*. "Use a hollow-wall anchor"
is useless; "drywall alone will not hold 15 kg — the anchor spreads the load
behind the board" changes behaviour.

## 7. Emergency and distress handling

If input suggests an active hazard — a smell of gas, sparking, water flooding, a
partially collapsed fixture, a described injury — the assistant abandons the task
flow immediately and returns fixed, human-written emergency guidance
(make safe, isolate if safe to do so, evacuate, contact emergency services or the
utility). No guide generation, no upsell, no "but if you'd like to continue".

These responses are static strings in the codebase, selected by a keyword and
classifier check on intake, and unit-tested.

## 8. Legal positioning

- The app provides **information**, not professional certification. Guides carry
  a persistent, plain-language disclaimer.
- Local codes and regulations vary; where a task is commonly regulated, the guide
  says so and names the check the user should make. `sites.country_code` drives
  which conventions and standards are referenced.
- Product-specific instructions from the manufacturer take precedence over
  HandyAI's, and every guide says so.
- We do not certify, inspect, or warrant work.

## 9. Incident process

A user-reported safety-critical error is a **P0**:

1. The specific guide revision is retrievable in full, with its analysis, prompt
   version, retrieved chunks, and `ai_calls` rows — enough to reconstruct exactly
   why the output happened.
2. Root cause is classified: rules gap, retrieval gap, extraction error, prompt
   defect, or validation gap.
3. The fix lands as a `safety_rules` row and/or a validation check, plus a
   regression fixture — never as a prompt tweak alone.
4. Affected users with the same pattern are identified from the analysis fields
   and notified.
5. The incident is recorded in `docs/safety-incidents.md` with the fix and the
   test that now prevents it.

## 10. Testing requirements

Safety code is the most heavily tested code in the repo:

- Unit tests for every `safety_rules` row: a case that matches, and a case that
  nearly matches and must not.
- Property test: no combination of analysis inputs produces green when any
  hard-red-line condition is present.
- Regression fixtures for known-dangerous scenarios (15 kg TV on plasterboard
  with drywall screws; unknown wall with a heavy load; ceiling mount) asserting
  the expected class.
- Post-validation tests asserting uncited safety numbers are stripped.
- A test asserting that the safety classification in a persisted guide always
  equals the classifier output for its analysis — belt and braces against a
  future refactor letting model text win.
