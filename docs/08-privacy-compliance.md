# 08 — Privacy & Store Compliance

These are build requirements tracked as tasks, not a pre-launch checklist. The
app photographs the inside of people's homes; that deserves a higher standard
than the legal minimum.

---

## 1. Data inventory

| Category | Data | Purpose | Legal basis (GDPR) | Retention |
| --- | --- | --- | --- | --- |
| Identity | email, display name, auth provider subject | account | contract | life of account |
| Content | photos, video frames, audio, text of the user's situation | generate the guide | consent | 24 months after last activity, or on request |
| Derived | analyses, guides, annotations, conversations | deliver and resume the service | contract | life of the project |
| Technical | device model, OS, app version, crash traces | stability | legitimate interest | 90 days |
| Usage | screen and funnel events (pseudonymous) | improve the product | consent | 14 months |
| Commercial | plan, IAP transaction ids, credit ledger | billing | contract / legal obligation | 7 years (tax) |
| Security | audit log with hashed IP | fraud and abuse | legitimate interest | 24 months |

**Not collected:** precise location, contacts, calendar, health, advertising id,
background activity of any kind. GPS EXIF is stripped on ingest and never
persisted.

## 2. Consent model

Separate, granular, revocable, versioned — no bundling.

| Consent | Required? | Effect if declined |
| --- | --- | --- |
| Terms of Service | yes | cannot create an account |
| Privacy Policy acknowledgement | yes | cannot create an account |
| `image_processing` | for the core feature | app is browse/read-only; no upload occurs |
| `analytics` | no | no product analytics events |
| `marketing` | no | no promotional messages |
| `model_improvement` | no, **default off** | user images are never used for model improvement unless explicitly opted in |

Every consent grant/revoke writes a `consents` row with version, timestamp,
hashed IP, and user agent. Every upload references the consent row that
authorised it, so authority for any stored image is provable.

The `image_processing` consent screen states plainly, before the first upload:
what is sent, to which category of processor, for how long it is kept, and that
it is not used for training by default.

## 3. Sub-processors and transfers

AI providers are sub-processors. Requirements:

- Named in the privacy policy with their purpose and region.
- Zero-retention or minimum-retention API terms wherever the provider offers
  them, with no training on our data.
- DPAs in place; SCCs for transfers out of the EEA/UK.
- A provider change is a privacy-policy update and an in-app notice, not a silent
  config change — this is another reason the provider abstraction exists.

Images are transmitted to providers over TLS, stripped of metadata, and
referenced by opaque keys with no user identifier in the filename.

## 4. User rights (all self-service, all in-app)

| Right | Implementation | SLA |
| --- | --- | --- |
| Access / portability | `POST /v1/account/export` → job → signed ZIP (JSON + original images), link expires in 7 days | < 24 h |
| Erasure | `POST /v1/account/delete` → 30-day grace → hard delete of every row and storage object | < 30 days |
| Rectification | edit profile; correct analysis findings; regenerate a guide | immediate |
| Restriction / objection | revoke consents individually | immediate |
| Withdraw consent | privacy dashboard toggles | immediate |
| Do Not Sell (CCPA) | we do not sell or share personal information; stated explicitly | n/a |

**Account deletion is reachable in two taps from Profile.** Apple requires
in-app deletion for any app with account creation; Play requires a web deletion
route too, so a public `handyai.app/delete-account` page performs the same
verified flow.

Deletion verification: the purge job asserts zero rows across every table
carrying `user_id` and zero objects under the user's storage prefix, then records
a completion entry containing no personal data.

## 5. Children

- Not directed at children; 13+ (16+ where local law requires).
- Age gate at sign-up; no behavioural profiling of anyone.
- If a minor account is detected, tool-based tasks are classified red — a policy
  in `safety_rules`, not a UI string.
- No third-party ad SDKs, ever. This keeps us out of the child-privacy
  ad-targeting minefield entirely.

## 6. Permissions

Requested in context, at the moment of use, with a purpose string that says
something real.

| Permission | When | Purpose string (short form) |
| --- | --- | --- |
| Camera | first capture | "To photograph what you're installing so we can analyse it." |
| Photo library | when adding an existing photo | "To let you choose photos of your project." (limited/selected access supported) |
| Microphone | when the user taps hold-to-talk | "To let you describe the problem out loud instead of typing." |
| Notifications | after the first analysis completes, not at launch | "To tell you when your guide is ready." |

Not requested: location, contacts, calendar, background refresh, tracking.
`NSUserTrackingUsageDescription` is absent because we do not track. Declining any
permission leaves a working app with a clearly explained reduced capability.

## 7. App Store (Apple) requirements

| Requirement | Our position |
| --- | --- |
| 2.1 completeness | demo account with seeded projects supplied for review |
| 4.2 minimum functionality | full native app; not a web wrapper |
| 5.1.1 data minimisation | only what the feature needs; no forced registration to browse |
| 5.1.1(v) account deletion | in-app, two taps, plus a web route |
| 5.1.2 purpose limitation | no data sharing with third parties for advertising |
| 1.2 UGC / AI content | reporting mechanism on generated content; safety layer documented; no user-to-user content in v1 |
| 3.1.1 IAP | subscriptions via StoreKit only; no external purchase links |
| Privacy nutrition labels | filled honestly: contact info, user content, identifiers (app-scoped), diagnostics — all "not used for tracking" |
| Privacy manifest | `PrivacyInfo.xcprivacy` declaring APIs and reasons; SDK manifests verified |
| Physical-risk guidance | persistent disclaimer, professional referral on red, no medical/electrical/gas instructions |

## 8. Google Play requirements

| Requirement | Our position |
| --- | --- |
| Data safety form | matches the inventory in §1 exactly; reviewed each release |
| Account deletion policy | in-app plus the public web route |
| Photo/video permissions | `READ_MEDIA_IMAGES` only when the user picks from the library; Photo Picker preferred, which needs no permission |
| Sensitive permissions | none declared; no `QUERY_ALL_PACKAGES`, no background location |
| Families policy | not in the Families programme; 13+ |
| AI-generated content policy | generated content is labelled; safety layer and reporting documented |
| Health/finance claims | none made |
| Target API level | current requirement at each release |

## 9. Security controls that back the privacy promises

- Tokens in Keychain / Keystore (`flutter_secure_storage`), never in
  `SharedPreferences`; short-lived access tokens; refresh rotation with reuse
  detection.
- TLS 1.2+ only, HSTS, certificate validation with no user-installable-CA trust
  in release builds.
- Storage objects private by default; access only via signed URLs with a 10-minute
  TTL and no directory listing.
- Argon2id password hashing; per-user salts; no password length ceiling.
- Secrets from the environment or a managed secret store; `.env` git-ignored;
  secret scanning in CI; keys rotatable without a code change.
- Rate limiting on every mutating endpoint; input validation via Pydantic at the
  boundary; parameterised SQL only.
- Append-only audit log for privileged and privacy-relevant actions.
- Postgres RLS as defence-in-depth behind application authorisation.
- No debug logging of image bytes, prompts containing user content, or tokens.
  Log redaction is a filter, not a convention.
- Dependency and container scanning in CI; a documented patch SLA.
- OWASP MASVS L1 as the baseline; annual third-party pen test before GA.

## 10. Transparency about AI

Stated in-app, not buried in a policy:

- Guides are AI-generated and can be wrong.
- Confidence is shown, not hidden.
- The safety classification and its reasoning are visible.
- Sources are cited when a guide relies on a manual or standard.
- Feedback on any guide or step is one tap, and feeds the regression suite.
- Images are not used to train models unless the user opts in.

## 11. Pre-launch compliance checklist

- [ ] Privacy policy and ToS published, versioned, and linked in-app and in both
      store listings
- [ ] Apple privacy nutrition labels completed and verified against §1
- [ ] `PrivacyInfo.xcprivacy` present; all SDKs supply manifests
- [ ] Play Data Safety form completed and verified against §1
- [ ] In-app account deletion verified end to end, including storage purge
- [ ] Public web account-deletion route live
- [ ] Data export verified to contain everything in §1 and nothing extra
- [ ] All permission purpose strings reviewed by a human for honesty
- [ ] DPAs and SCCs signed with every sub-processor
- [ ] Retention jobs deployed, scheduled, and observed to actually delete
- [ ] Secret scanning, dependency scanning, and container scanning green
- [ ] Pen test findings triaged; no open highs
- [ ] Reviewer demo account seeded
