# User Stories Log — work beyond the original 6-stage plan

This log records user stories and scope additions that are **not part of the original plan**
(`PLAN.md`, Etapy 1–6). Each story gets an ID, its source, status and links to the
implementation plan, chunks and commits.

**Statuses:** `PROPOSED` (captured, not planned) · `PLANNED` (implementation plan exists) ·
`IN PROGRESS` · `DONE` (all chunks ticked, tests green) · `DROPPED`.

**How to add an entry:** append a new `US-NN` section, add a row to the summary table, and, once
planned, add its chunks to `PLAN.md` under *Rozszerzenia poza pierwotny plan*. When a chunk is
committed, add the commit hash to the story's *Delivery* list.

## Summary

| ID | Story | Status | Plan | Added |
|----|-------|--------|------|-------|
| [US-01](#us-01) | Administrator configures LLM models (local / cloud) in the UI and tests them | DONE | [07_LLM_MODEL_CONFIG.md](implementation/07_LLM_MODEL_CONFIG.md) · Etap 7 | 2026-10-20 |
| [US-02](#us-02) | Administrator manages users: create accounts, reset passwords, block access, force password change, manage roles | DONE | [08_USER_MANAGEMENT.md](implementation/08_USER_MANAGEMENT.md) · Etap 8 | 2026-10-21 |
| [US-03](#us-03) | Member adds photos with a description, marks and describes elements on them; experts verify; the atlas powers image recognition | DONE | [09_IMAGE_ATLAS.md](implementation/09_IMAGE_ATLAS.md) · Etap 9 | 2026-10-21 |
| [US-04](#us-04) | Every model (LLM, embeddings, speech, OCR, image embeddings) is configured and provided over an API — local (LM Studio) or cloud; no built-in model required | DONE | [10_MODEL_PROVIDERS_API.md](implementation/10_MODEL_PROVIDERS_API.md) · Etap 10 | 2026-10-22 |
| [US-05](#us-05) | Administrator removes an LLM provider that is no longer used, so unused configurations and keys do not stay | DONE | [11_PROVIDER_REMOVAL.md](implementation/11_PROVIDER_REMOVAL.md) · Etap 11 | 2026-10-26 |
| [US-06](#us-06) | The UI works like Open Notebook — menu on the left, workspace on the right — on desktops and phones | DONE | [12_APP_SHELL_RESPONSIVE.md](implementation/12_APP_SHELL_RESPONSIVE.md) · Etap 12 | 2026-10-26 |
| [US-07](#us-07) | Administrator brands the UI: organisation name, logo (and favicon) and colours | DONE | [13_BRANDING.md](implementation/13_BRANDING.md) · Etap 13 | 2026-10-26 |
| [US-08](#us-08) | User selects the UI language (Polish or English); the UI reloads in the chosen language | DONE | [14_UI_LANGUAGE.md](implementation/14_UI_LANGUAGE.md) · Etap 14 | 2026-10-26 |
| [US-09](#us-09) | Article review dates and freshness: overdue articles warn and stay in chat; experts reconfirm them | PLANNED | [15_ARTICLE_REVIEW_DATES.md](implementation/15_ARTICLE_REVIEW_DATES.md) · Etap 15 | 2026-11-05 |

---

## US-01

**Story:** As an administrator I would like to be able to configure LLM models in the UI, so that
I can decide whether to use locally hosted or cloud models and test different models with an easy
way to change the configuration.

- **Source:** product owner request, 2026-10-20.
- **Status:** DONE 2026-10-20 (Etap 7, chunks 7.1–7.7). Implemented on request while 6.7 and
  6.8 are still open.
- **Plan:** [docs/implementation/07_LLM_MODEL_CONFIG.md](implementation/07_LLM_MODEL_CONFIG.md)
- **Acceptance criteria:**
  1. An admin (with MFA) can add, edit, test and disable local and cloud model providers in
     `/admin/llm`; API keys are write-only and stored encrypted.
  2. An admin can assign a model per purpose (RAG, promotion, private notes, PII scrubbing,
     vision, embeddings); purposes that handle private data can only use local models, enforced in
     the UI, the API, the database and the gateway.
  3. Cloud models can be used only if the operator allows it in the server configuration; the UI
     can narrow but never widen that policy.
  4. Changes are made in a draft revision, tested (connection + probe), activated without
     restarting services, and can be rolled back in one click.
  5. A playground compares up to 3 models side by side (answer, latency, tokens, JSON validity)
     without using any knowledge-base or private content.
  6. Every change and playground use is recorded in the security audit log.
- **Out of scope / follow-ups:** live switching of the embedding model (needs re-embedding of the
  index, candidate follow-up story); per-user model choice; cost reporting.
- **Delivery:**
  - 7.1 data model, encrypted keys, locality triggers, seeding: `068ff49`
  - 7.2 config store, hot reload, per-purpose gateway: `9fca464`
  - 7.3 `llm-egress` CONNECT proxy: `6be5a4e`
  - 7.4 `/admin/llm` API, connection tests, probes: `83e1b3f`
  - 7.5 playground and model comparison: `5849ac2`
  - 7.6 `/admin/llm` console: `d0173aa`
  - 7.7 E2E scenario 9, security review, docs: the `feat(stage-7.7)` commit

---

## US-02

**Story:** As an administrator I want to manage users so that I can create new user accounts,
reset passwords, block access for a user, force a password change and manage user roles.

- **Source:** product owner request, 2026-10-21.
- **Status:** DONE 2026-10-21 (Etap 8, chunks 8.1–8.6). Implemented on request while 6.7 and
  6.8 are still open.
- **Plan:** [docs/implementation/08_USER_MANAGEMENT.md](implementation/08_USER_MANAGEMENT.md)
- **Acceptance criteria:**
  1. An admin (with MFA) can create an account with username, email, name and roles in
     `/admin/users`; the user sets their own password at first login (email link or a one-time
     temporary password), and EXPERT/ADMIN users must also enrol TOTP.
  2. An admin can reset a user's password by email link or temporary password; the app never
     stores, logs or audits a password.
  3. An admin can block and unblock a user: Keycloak refuses new logins and every live session
     ends at once. Admin blocks and shield locks are kept apart.
  4. An admin can force a password change at the next login, optionally signing the user out now.
  5. An admin can grant and remove `MEMBER`, `EXPERT`, `ADMIN`, effective on the user's next
     request. Granting EXPERT/ADMIN needs a fresh second factor; nobody can remove their own
     ADMIN role, block themselves, or demote or block the last admin.
  6. Every action is recorded in the security audit log.
- **Out of scope / follow-ups:** deleting users / GDPR erasure; resetting a user's TOTP device;
  bulk import and invitations; a separate user-admin role.
- **Delivery:**
  - 8.1 Keycloak admin client, `spoldzielnia-admin` service account, realm setup: `a535b6e`
  - 8.2 block columns, per-user session index, audit events: `936306a`
  - 8.3 `UserAdminService` and guardrails: `2cc0003`
  - 8.4 `/admin/users` API: `f877919`
  - 8.5 `/admin/users` console: `4149fe7`
  - 8.6 E2E scenario 10, security review, docs: the `feat(stage-8.6)` commit
- **Found while implementing:** the realm's user profile requires e-mail, first and last name;
  an account created without them is stopped at its first login by Keycloak's profile form, so
  the console now requires all three (acceptance criterion 1 reads "username, email, name").

---

## US-03

**Story:** As a member I want to add a photo with a description, mark one or more elements on it
and describe them, so that the cooperative builds verified knowledge for image recognition (for
example, a beekeeper adds photos of queen bees).

The solution is multi-domain: beekeeping is only an example use case.

- **Source:** product owner request, 2026-10-21 (refinement session).
- **Status:** DONE 2026-10-21 (Etap 9, chunks 9.1–9.10). Implemented on request while 6.7 and
  6.8 are still open.
- **Plan:** [docs/implementation/09_IMAGE_ATLAS.md](implementation/09_IMAGE_ATLAS.md)
- **Refinement decisions:** both a dataset and in-app recognition, phased (Phase A dataset,
  Phase B recognition); photos are annotated privately and then contributed to a shared atlas
  through expert review; labels come from an expert-curated vocabulary (members propose) plus a
  free-text description; rectangles only. Answers 2026-10-21: multi-domain (labels grouped
  in domains, no beekeeping-specific seed); the author decides per photo whether their name is
  shown; dataset export needs a separate opt-in per photo.
- **Acceptance criteria:**
  1. A member can open a photo in their private notebook, write a caption, draw rectangles on
     elements, and give each one a label from the vocabulary and a description (mouse, touch and
     keyboard). Nobody else can see the photo or its annotations.
  2. Admins create domains (e.g. beekeeping, gardening); experts curate each domain's label
     vocabulary; members can propose a missing label.
  3. A member can send an annotated photo to the atlas. The shared copy has no metadata
     (including GPS), texts are PII-scrubbed and scanned for injection, and photos showing
     people or documents are flagged before sending.
  4. Two experts verify a photo (they can correct boxes and labels) before it appears in the
     shared atlas; members browse approved photos by label.
  5. The author decides per photo whether their name is shown or the photo is anonymous, and
     can change it any time. The author can suspend, resume or revoke each atlas photo and
     separately allow or refuse its use in dataset exports (off unless ticked); revoke removes the image, its marks and its vectors.
  6. An admin can export the verified, consented dataset (COCO/YOLO + manifest) only if the
     operator enabled it, with step-up MFA; every export is audited.
  7. (Phase B) „Rozpoznaj” on a notebook photo suggests marked elements with boxes, citing
     verified atlas photos of the same domain, using local models only; with no similar verified examples it says
     so instead of guessing. Suggestions can be accepted as the member's own annotations.
- **Out of scope / follow-ups:** polygons and points; automatic face blurring; training a
  detector inside the app; video frames; domain-scoped experts.
- **Delivery:**
  - 9.1 domains and label vocabulary: `588cd2c`
  - 9.2 private photo annotations and image serving: `73cdd45`
  - 9.3 annotator UI in the notebook: `f2a93dd`
  - 9.4 contribute to the atlas: `38773d3`
  - 9.5 expert review and the shared atlas: `d889e77`
  - 9.6 consent, attribution and export opt-in: `d6ca6bf`
  - 9.7 dataset export (COCO/YOLO): `7664724`
  - 9.8 visual embeddings and `atlas_visual_index`: `4a52f77`
  - 9.9 recognition „Rozpoznaj”: `a9b9006`
  - 9.10 E2E scenario 11, security review, docs: the `feat(stage-9.10)` commit

---

## Scope additions inside the original stages (not separate user stories)

Changes that went beyond the letter of the chunk specs while implementing Stages 1–6. They
are recorded here so the difference between the plan and the delivered system stays visible.

| Date | Commit | Stage | Addition | Why |
|------|--------|-------|----------|-----|
| 2026-10-19 | `7bd2b09` | 3.4 | `llm.structured_output = json_schema \| prompt`, tolerant JSON extraction, `LocalModelRejected` for non-retryable 4xx. | Live promotion with LM Studio + Bielik failed: its grammar sampler returns 400 for any `json_schema` response format. |
| 2026-10-19 | `b7a7f8b` | 6.7 | Prompt mode sends an example object + field notes instead of the raw schema, schema-echo recovery, citation marker clean-up. | Local models echoed the JSON Schema back instead of filling it. |
| 2026-10-19 | `b7a7f8b` | 6.7 | Second expert account (`expert2`) in the Keycloak realm export; smoke check that the embedding model separates unrelated texts. | Review quorum needs two experts in E2E; a broken embedding model silently ruined retrieval. |
| 2026-10-23 | `feat(stage-6.7)` | 6.7 | Dev embedding model switched to EmbeddingGemma through the console path (probe, activation, new index generation); E2E seed `retire_topic`, shield reset in global setup; Pillow 13. | Qwen3-Embedding GGUF in LM Studio is degenerate (cosine 0.944 for unrelated texts); reruns competed with their own earlier articles and raised members' shield risk; Pillow 12.3 had 35 advisories. |
| 2026-10-23 | `feat(stage-6.8)` | 6.8 | Production realm import without test users (`make prod-realm`), `make create-admin`, Keycloak hostname from `SITE_DOMAIN`, rollback limited to the same schema head. | A bare-metal install had no realm and no way to create the first admin; an older API is never ready on a newer schema. |

---

## US-04

**Story:** Spółdzielnia AI should not need a local LLM or embedding model. Everything should be
configurable and provided over an API: a model run locally (e.g. in LM Studio) or in the cloud.

- **Source:** product owner request, 2026-10-22 (refinement session, after a hosting question
  about a GPU-less VPS and Mistral's API).
- **Status:** DONE 2026-10-23 (10.1–10.3 on 2026-10-22, 10.4–10.8 on 2026-10-23).
- **Plan:** [docs/implementation/10_MODEL_PROVIDERS_API.md](implementation/10_MODEL_PROVIDERS_API.md)
- **Refinement decisions:** private data goes only to providers an admin has cleared for it
  (operator env list as the ceiling; replaces "local only" in AGENTS.md rule 4); image
  embeddings via an API or the optional built-in SigLIP; speech-to-text and document OCR move to
  APIs, the injection classifier stays built in; a missing model turns the feature off with a
  clear message and work waits, with no fallback to another provider.
- **Acceptance criteria:**
  1. The default stack starts and is healthy with no model server and no downloaded models.
  2. An admin configures every purpose (chat, vision, text and image embeddings, speech-to-text,
     OCR) in `/admin/llm` with local (LM Studio, Ollama, vLLM…) or cloud providers.
  3. Private data reaches only providers cleared for it, within the operator's env list,
     enforced in the UI, the API, the database and the gateway.
  4. Without a configured model the feature shows „Model nie jest skonfigurowany”; waiting work
     completes once a model is configured; no other provider is ever used instead.
  5. Changing the embedding model re-embeds into a new index and swaps without downtime or mixed
     vector spaces.
  6. Members can see which providers process their private data.
- **Delivery:** `88da0fc` plan · `bf71612` 10.1 clearance trust model · `faa6c3b` 10.2 "not
  configured" and waiting work · `e901b35` 10.3 versioned text index · 10.4 speech-to-text over
  an API (`879a9ae`) · `3585128` 10.5 documents without models, OCR over an API · `05e3eba` 10.6 image embeddings
  over an API or the built-in SigLIP, the atlas on index generations · `f339bf5` 10.7 no model
  server by default, API-only profile · 10.8 transparency page and banner, index panel, E2E
  scenario 12, security review (this commit).

---

## US-05

**Story:** As an administrator I would like to remove an LLM provider, so that I don't have to
keep unused configurations.

- **Source:** product owner request, 2026-10-26. Test scenario given: "Given admin1 logged in,
  when see list of LLM providers, then admin can remove config and gets refreshed list."
- **Status:** DONE 2026-10-26.
- **Plan:** [docs/implementation/11_PROVIDER_REMOVAL.md](implementation/11_PROVIDER_REMOVAL.md)
- **Refinement decisions (accepted 2026-10-26):** a provider that a past revision used is
  archived instead of deleted (history and rollback stay intact; the key is erased, the
  clearance revoked, it can never be routed to again, its name is free); removal is refused
  while the active revision, a serving/building index generation or an open draft uses it; no
  MFA step-up, since removal only narrows where data can go.
- **Acceptance criteria:**
  1. admin1 sees „Usuń” next to each provider; confirming removes it and the list refreshes.
  2. A provider in use has a disabled „Usuń” with the reason; the API answers 409.
  3. A provider from older revisions is archived: hidden, no key, no clearance; History still
     names it; rollback to those revisions is refused.
  4. Every removal is audited (`LLM_PROVIDER_DELETED`), without the key.
- **Delivery:** 11.1 (this commit).

---

## US-06

**Story:** Let's upgrade the UI. It should be like Open Notebook (github.com/lfnovo/open-notebook):
the menu on the left side, the workspace on the right taking 2/3 of the visible window. The UI
has to support mobile devices.

- **Source:** product owner request, 2026-10-26.
- **Status:** DONE 2026-10-26 (12.1–12.8, one commit).
- **Plan:** [docs/implementation/12_APP_SHELL_RESPONSIVE.md](implementation/12_APP_SHELL_RESPONSIVE.md)
- **Refinement decisions (accepted 2026-10-26):** a fixed 16rem menu that collapses to an icon
  rail, so the workspace gets at least 2/3 of the width (not a literal 1/3 menu); a new shell
  plus a restyle of every page; a left drawer on phones; the note page uses Open Notebook's
  Źródła | Notatka | Zapytaj columns (tabs on a phone); only the layout patterns are borrowed,
  not code or branding; the CSP stays unchanged; no theme switch or command palette in this story.
- **Acceptance criteria:**
  1. On desktop the menu is on the left and the workspace takes at least 2/3 of the width; only
     the workspace scrolls.
  2. The menu collapses to an icon rail; the choice survives a reload without a layout jump.
  3. Menu sections follow the account's roles (member, EXPERT, ADMIN).
  4. On a phone a top bar opens the menu as a drawer that closes on navigation, Escape and the
     backdrop.
  5. The note page shows Źródła | Notatka | Zapytaj as columns on desktop and tabs on a phone.
  6. No page scrolls sideways at 360px; touch targets are at least 44px; axe reports no serious
     or critical issues.
- **Delivery:** `db3fc43` plan · 12.1–12.8 shell, building blocks, every page restyled, E2E scenario 14 on desktop, tablet and phone, axe (this commit).

---

## US-07

**Story:** As an administrator I would like to be able to configure the UI, so that I can set up
the logo of my organisation and change colours.

- **Source:** product owner request, 2026-10-26.
- **Status:** DONE 2026-10-26 (13.1–13.6, one commit).
- **Plan:** [docs/implementation/13_BRANDING.md](implementation/13_BRANDING.md)
- **Refinement decisions (accepted 2026-10-26):** the organisation name, logo and a favicon
  generated from it can be branded (the Keycloak sign-in page cannot, in this story); the admin
  picks a brand and an accent colour and every other shade, including dark mode, is derived with
  WCAG AA contrast enforced; background, text and status colours stay fixed; only PNG, JPEG or
  WebP logos, re-encoded (SVG refused); colours reach the page through a nonced `<style>`, so the
  CSP is unchanged.
- **Acceptance criteria:**
  1. admin1 (with MFA) sets the name, logo and two colours in `/admin/branding` with a light and
     dark preview; after saving, every user sees them on the next page load without a flash of
     the old colours; the tab title and favicon follow.
  2. Colours that would make text unreadable are adjusted (and the admin is told) or refused.
  3. SVG, GIF, oversized and disguised files are refused; stored images carry no metadata.
  4. „Przywróć domyślne” restores the original look for everyone.
  5. Only an ADMIN with MFA can change branding; anyone can read it. Every change is audited.
- **Delivery:** `4033c26` plan · 13.1–13.6 palette derivation, migration 0027, logo pipeline, API, branded layout and menu, „Wygląd” console, E2E scenario 15 (this commit).

---

## US-08

**Story:** As a user I want to select the UI language, so that I can use the application in
English. Given a user, when the user switches the language, the UI reloads and the interface
is in the chosen language.

- **Source:** product owner request, 2026-10-26.
- **Status:** DONE 2026-10-27 (14.1–14.6, one commit).
- **Plan:** [docs/implementation/14_UI_LANGUAGE.md](implementation/14_UI_LANGUAGE.md)
- **Refinement decisions (accepted 2026-10-26):** Polish (default, always, until the user
  chooses) and English; every screen, message, date and number plus the server's fixed texts
  are translated, and chat answers are written in the chosen language; content (notes, wiki,
  atlas, the organisation name) and the Keycloak pages are not; the choice is stored on the
  account and applied through a `lang` cookie, so it follows the user to every device; no
  language prefixes in URLs.
- **Acceptance criteria:**
  1. A user switches between „Polski” and „English” in the menu; the page reloads on the same
     address in the chosen language, `<html lang>` and the tab title included.
  2. The choice follows the account: another device is in that language from its first page.
  3. The server's fixed texts and chat answers are in the chosen language; English answers pass
     the same verbatim and watermark checks as Polish ones (fail-closed otherwise).
  4. No user-visible string is hard-coded in the frontend; the two catalogues have the same keys
     and placeholders; existing E2E scenarios pass in Polish.
- **Delivery:** `14bef00` plan · 14.1–14.6 locale on the account and the `lang` cookie (migration 0028), server texts in both languages, English answers with a cross-language verbatim check and an English watermark, `next-intl` catalogues for every screen, the language switch, E2E scenario 16 (this commit).

---

## US-09

**Story:** As a member I want to see whether an article has been checked recently, and as an expert I want
overdue articles brought to my attention, so that old knowledge is reviewed without disappearing.

- **Source:** product owner request, 2026-11-05, after comparing the project with [ICS](https://github.com/inprojectspl/ics).
- **Status:** PLANNED 2026-11-05.
- **Plan:** [docs/implementation/15_ARTICLE_REVIEW_DATES.md](implementation/15_ARTICLE_REVIEW_DATES.md)
- **Acceptance criteria:**
  1. Overdue articles stay CANONICAL and in chat, with a warning on the page and in citations.
  2. The period is 12 months by default (admin setting), or 1-60 months per article (expert).
  3. A non-author expert with MFA can confirm an article is still current, with no new version or index change.
  4. Experts have an overdue list; honeypots never appear on it.
- **Delivery:** plan (this commit).
