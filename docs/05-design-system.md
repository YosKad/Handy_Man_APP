# 05 — Design System & UX

Dark mode first, light mode fully supported. The feeling to hit: **calm,
confident, precise.** The user is often stressed, holding a drill, in a hallway.
The interface should reduce their cognitive load, not compete for attention.

Reference points: Apple HIG for structure and accessibility, Linear for density
and typography discipline, Arc for warmth in motion, Teenage Engineering /
Nothing for restrained use of accent.

---

## 1. Design principles

1. **One decision per screen.** Never present two primary actions.
2. **Progress is always visible.** Nothing spins without saying what it's doing.
3. **The photo is the hero.** The user's own image is the largest element on any
   step screen; chrome recedes.
4. **Safety is typographic, not decorative.** Warnings use weight, colour, and
   position — never sirens, never scare-graphics.
5. **Say why.** Every question the app asks shows its reason inline.
6. **Motion clarifies causality.** Transitions show where a thing came from;
   nothing moves for delight alone.
7. **Nothing is unreachable one-handed.** Primary actions live in the bottom
   third.

## 2. Colour tokens

Semantic names only — components never reference a raw hex value.

### Dark (default)

| Token | Value | Use |
| --- | --- | --- |
| `bg.canvas` | `#0B0B0F` | app background |
| `bg.surface` | `#14141A` | cards, sheets |
| `bg.surfaceRaised` | `#1C1C24` | elevated cards, inputs |
| `bg.overlay` | `#000000` @ 60% | modal scrim |
| `border.subtle` | `#FFFFFF` @ 8% | hairlines |
| `border.strong` | `#FFFFFF` @ 16% | focused inputs |
| `text.primary` | `#F5F5F7` | body, titles |
| `text.secondary` | `#A0A0AB` | supporting copy |
| `text.tertiary` | `#7F7F8B` | metadata |
| `accent.primary` | `#3D7BFF` | primary actions, active state |
| `accent.onPrimary` | `#FFFFFF` | text on accent |
| `accent.muted` | `#3D7BFF` @ 14% | accent surfaces |

### Light

`bg.canvas #FBFBFD`, `bg.surface #FFFFFF`, `bg.surfaceRaised #F4F4F7`,
`text.primary #0B0B0F`, `text.secondary #55555F`, `text.tertiary #6E6E78`,
`border.subtle #0B0B0F` @ 8%, `accent.primary #2563EB`.

### Safety palette

The hue is used for strokes, dots and glyphs. Readable text uses a separate
**ink** token, because the hue alone does not clear 4.5:1 on every fill — see
the amendment note below.

| Class | Fill (dark / light) | Hue | Ink (dark / light) | Meaning |
| --- | --- | --- | --- | --- |
| `safety.green` | `#0F3D2E` / `#E7F7F0` | `#22C55E` | `#22C55E` / `#15803D` | Safe DIY |
| `safety.yellow` | `#40320B` / `#FDF6E3` | `#F5A524` | `#F5A524` / `#8A5A00` | Proceed carefully |
| `safety.red` | `#45141A` / `#FDECEE` | `#EF4444` | `#FF7B7B` / `#B4232B` | Professional recommended |

> **Amendment, 2026-08-05.** Building the prototype in
> [`prototype/screens.html`](prototype/screens.html) and measuring it found three
> combinations below the 4.5:1 this document requires: `text.tertiary` at 3.9:1
> on the dark canvas and 3.31:1 on the light one, and `safety.red` as text on its
> own dark fill at 4.08:1. The values above are the corrected ones. Both themes
> now measure clean across every chip, caution and notice. This is why the
> prototype exists before the Flutter code does.

Safety state is **never** communicated by colour alone: every badge pairs the
colour with an icon and a word.

## 3. Typography

Inter (or SF Pro on iOS via system fallback). Tabular figures for measurements.

| Style | Size / Line | Weight | Use |
| --- | --- | --- | --- |
| `display` | 34 / 40 | 700, -0.5 tracking | onboarding, empty states |
| `titleLarge` | 28 / 34 | 700 | screen titles |
| `title` | 22 / 28 | 600 | section headers, step titles |
| `bodyLarge` | 17 / 26 | 400 | step instructions |
| `body` | 15 / 22 | 400 | default |
| `label` | 13 / 18 | 500 | buttons, chips |
| `caption` | 12 / 16 | 500, +0.2 | metadata, confidence |
| `mono` | 13 / 20 | 500 | measurements, model numbers |

Step instruction text is `bodyLarge` at 26 line-height because it is read at
arm's length while holding a tool. All sizes scale with the OS text-size setting
up to 200% without clipping — enforced by golden tests at 1.0×, 1.5×, 2.0×.

## 4. Spacing, radius, elevation

- **Spacing scale (4-based):** 2, 4, 8, 12, 16, 20, 24, 32, 40, 56, 72.
  Screen gutter 20. Card padding 16–20. Section gap 32.
- **Radius:** `sm 8` (chips), `md 12` (inputs), `lg 16` (cards),
  `xl 24` (sheets, media), `full` (pills, FAB).
- **Elevation:** three levels only, expressed as large soft shadows in light mode
  and as surface-lightening in dark mode (shadows are near-invisible on dark, so
  hierarchy comes from surface tone plus a 1px subtle border).

## 5. Motion

| Token | Duration | Curve | Use |
| --- | --- | --- | --- |
| `micro` | 120 ms | `easeOut` | taps, toggles |
| `standard` | 240 ms | `easeInOutCubic` | sheet, card expand |
| `hero` | 420 ms | `easeOutExpo` | photo → step transition |
| `ambient` | 1600 ms loop | `easeInOut` | analysis progress shimmer |

Every animation honours `MediaQuery.disableAnimations` (Reduce Motion): it
becomes an instant state change, never a still-running loop.

## 6. Components

Each lives in `mobile/lib/shared/design_system/` with a widgetbook entry.

- **`AppButton`** — `primary` (filled accent), `secondary` (surfaceRaised),
  `ghost` (text), `destructive`. Sizes `lg` (56 h, full width) and `md` (44 h).
  Built-in loading state that preserves width so the layout never jumps.
- **`AppCard`** — surface + radius `lg` + subtle border; optional `onTap` with a
  0.98 scale press.
- **`StepCard`** — ordinal badge, title, duration, safety chip, expandable body,
  evidence-photo slot, done/skip actions. Collapsed height is fixed so the
  timeline doesn't reflow while scrolling.
- **`SafetyBadge`** — icon + word + colour; three sizes; used inline and in
  headers.
- **`ConfidenceMeter`** — thin bar plus a percentage in `mono`; below the
  threshold it flips to "needs another photo" with the reason.
- **`CaptureFrame`** — camera viewfinder with a role prompt
  ("photograph the wall"), a subject-fill guide, torch, and a shot tray.
- **`MediaThumbGrid`** — 3-up grid with role labels and a retake affordance.
- **`ChatBubble`** — user (accent-muted, right) / assistant (surface, left);
  supports images, streaming caret, and a reference footer.
- **`ChatComposer`** — expanding text field, camera, photo library, mic
  (hold-to-talk), send. Never taller than 40% of the screen.
- **`ToolChip`** — tool/material with an owned/needed toggle.
- **`ProgressTimeline`** — vertical rail; done steps fill green, current step is
  accent with a pulse, future steps are tertiary.
- **`AppSheet`** — bottom sheet with a drag handle, radius `xl`, and safe-area
  padding.
- **`EmptyState`** — glyph, `display` line, one supporting line, one action.
- **`ErrorState`** — human message from the error envelope, a retry action, and
  a "copy request id" affordance in debug builds.
- **`Skeleton`** — shimmer placeholders matching final layout metrics exactly so
  content does not shift on load.

## 7. Screens

### 7.1 Onboarding (4 screens)

1. **Value** — one sentence, one image: a wall with an annotated overlay.
2. **How it works** — three lines: photograph → we analyse → you get your guide.
3. **Consent** — plain-language explanation of image processing with an explicit
   toggle. The app is usable in read-only mode if declined; nothing is uploaded
   without it.
4. **Permissions** — camera requested *in context* at first capture, not here.
   This screen only explains what will be asked and why.

No account required to browse; sign-in is requested at the first save.

### 7.2 Home

```
┌────────────────────────────────┐
│  Good evening                  │   greeting + avatar
│                                │
│  ┌──────────────────────────┐  │   CONTINUE (only when one exists)
│  │ ▓▓▓▓▓▓░░░░  Step 4 of 9  │  │   thumbnail, title, progress, Resume
│  │ Mount 55" TV · drywall   │  │
│  └──────────────────────────┘  │
│                                │
│  What are we doing?            │
│  ┌────────┐┌────────┐┌───────┐ │   three large action cards
│  │Install ││ Repair ││Advise │ │
│  └────────┘└────────┘└───────┘ │
│                                │
│  Recent                        │   horizontal cards w/ safety badges
│  Suggested for your home       │   optional, dismissible
└──────────── ⊕ ────────────────┘   FAB = quick scan (camera)
```

The FAB opens the camera directly. Fastest path from intent to capture is one
tap.

### 7.3 Capture

Full-bleed viewfinder. A single line at the top states what to photograph and
why. A shot tray at the bottom shows captured roles with checkmarks. "Analyse"
activates as soon as the required roles are covered — the app tells the user when
it has enough, rather than making them guess.

### 7.4 Analysis / confirm

Progress with named stages ("reading the label", "checking the wall"), then a
**confirmation card**: "Here's what I see" — product, model, weight, wall,
mount, each with a confidence meter and an edit affordance. Below it, any
`pending_requests` as photo-request cards and any `clarifications` as
single-question cards. Nothing proceeds until safety-critical fields clear their
thresholds.

### 7.5 Project

Header: title, safety badge, difficulty, estimated time, tools summary.
Then `ProgressTimeline` of `StepCard`s. The current step is expanded; completed
steps collapse to a single green line. Each step offers "photograph this" for
validation, and the assistant is one tap away with the current step already in
context.

### 7.6 Assistant

Message list with the project context pinned at the top ("Talking about: Mount
55" TV — Step 4"). Streaming replies. Attachments inline. Suggested prompts
appear only when the composer is empty.

### 7.7 History & Profile

History: grouped by month, filterable by workflow and safety class, searchable.
Profile: account, plan and credits, preferences (units, theme, locale),
notifications, **Privacy dashboard** (consents, export, delete), help, legal.

## 8. Accessibility requirements

- Every interactive element has a semantic label and a ≥ 44×44 pt target.
- Images carry meaningful labels; annotated step images expose the annotation as
  text ("arrow pointing to the top-left mounting hole").
- Focus order matches visual order; full keyboard/switch-control support.
- Contrast ≥ 4.5:1 for text, ≥ 3:1 for UI glyphs, in both themes.
- Screen-reader announcements for job progress and step completion, via live
  regions rather than silent visual change.
- Reduce Motion and Reduce Transparency respected.
- No information conveyed by colour alone anywhere in the app.

## 9. Implementation notes

- Tokens live in `core/theme/tokens.dart` as `const`; `ThemeData` is built from
  them in `core/theme/app_theme.dart`. **No hardcoded colours, sizes, or
  durations in feature code** — enforced by a custom lint.
- Golden tests cover every design-system component in light and dark at 1.0× and
  2.0× text scale.
- Widgetbook build published from CI so design review does not require a
  toolchain.
