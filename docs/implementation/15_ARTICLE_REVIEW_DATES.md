# Stage 15 — Article review dates and freshness (US-09)

> **Story ([US-09](../USER_STORIES_LOG.md#us-09)):** as a member I want to see whether an article
> has been checked recently, and as an expert I want overdue articles brought to my attention, so
> that old knowledge is reviewed without disappearing from the cooperative.

## Refined requirement

Refinement decisions (accepted 2026-11-05):

| Topic | Decision |
|---|---|
| Overdue behaviour | **Warn and stay.** An overdue article stays CANONICAL, in the wiki and in chat. It shows a warning on the page and in citations, and joins the experts' overdue list. |
| Review period | **12 months by default**, changed by an admin in the UI. An expert may set 1-60 months for one article. |
| When the clock restarts | On approval, and on a new „Nadal aktualny” (still current) action that needs no new version. |
| Who confirms (default, change on request) | **One expert who is not an author**, with MFA, even if `review_quorum` is higher: nothing in the text changes, so no new approval round is needed. Replacing the article still needs the full quorum. |
| Out of scope | Automatic removal from chat, per-category periods, reminders (Stage 17 adds them), dependencies between articles (Stage 18). |

## Acceptance criteria

```gherkin
Background: expert1 is logged in with MFA; the default review period is 12 months

Scenario: approval sets the review date
  When expert1 approves an article without choosing a period
  Then its review date is 12 months after the approval
  When expert1 approves another article and chooses 6 months
  Then its review date is 6 months after the approval

Scenario: an overdue article stays usable and shows a warning
  Given a canonical article whose review date has passed
  Then member1 sees "Wymaga przeglądu" in the wiki list and on the article page
  And the article is still retrieved by the cooperative chat
  And its citation carries the same warning
  And it is on expert1's overdue list, oldest first

Scenario: an expert confirms an article is still current
  When expert1 clicks "Nadal aktualny" on an overdue article they did not write
  Then its verification date is today and its review date moves forward by its period
  And its version, status and index entries do not change
  And the audit log records ARTICLE_RECONFIRMED without any article text

Scenario: authors and stale tabs are refused
  When an author of the article tries to confirm it
  Then the API answers 403 own_article
  When an expert confirms a version older than the current one
  Then the API answers 409 version_conflict

Scenario: an admin changes the default period
  When admin1 sets the default to 24 months
  Then later approvals use 24 months
  And existing review dates do not move
  And the audit log records WIKI_SETTINGS_CHANGED
```

## Design

### Data (migration `0029_article_review_dates`)

- `wiki_article.review_interval_months smallint NULL`, CHECK 1-60: the per-article override. NULL
  means the default in force at the next approval or confirmation.
- `wiki_article.review_due_at timestamptz NULL`, with a partial index for canonical, visible
  articles.
- `wiki_settings`: one row (`id = 1`, as in `branding`), `default_review_months smallint`, CHECK
  1-60, default 12.
- Backfill: visible CANONICAL articles get `coalesce(last_verified_at, status_changed_at)` + 12
  months. Installations with old articles may therefore see some overdue at once; that is accurate.
- Register the table and the migration in `tests/unit/test_db_models.py`.

### Rules

- **R1. Freshness is calculated, not stored.** `wiki/freshness.py` holds the pure function
  `freshness(article, now) -> "superseded" | "review_overdue" | "current"` and its SQL equivalent
  for the overdue list. A stored status would need a job to keep it true. Stage 18 adds one value.
- **R2. One place sets the date.** `ReviewService.decide` sets `review_due_at` next to
  `last_verified_at` on the final approval (including DISPUTED to CANONICAL). `ReviewIn` gains an
  optional `review_interval_months`. An approval short of the quorum stores nothing on the article.
- **R3. Confirmation is not an approval.** `POST /reviews/{article_id}/reconfirm` (expert + MFA +
  CSRF) takes `{version, review_interval_months?}`, locks the row like `decide`, refuses authors and
  old versions, and accepts only CANONICAL. It writes `last_verified_at`, `review_due_at` and the
  audit event. It returns no vector operations, adds no `ExpertReview`, and leaves
  `approving_experts` in citations unchanged. A confirmation may happen before the due date.
- **R4. Retrieval does not change.** `rag/service.py` adds `review_overdue: bool` to `Citation`; the
  warning is rendered by the frontend. Nothing about freshness goes to the model, so the prompt
  versions stay as they are and Anti-Distillation has no new text output.
- **R5. Honeypots stay invisible.** Hidden articles keep `review_due_at` NULL, are always `current`,
  and never appear on the overdue list. Since most real articles are also current, this does not
  make them stand out.
- **R6. Codes, not sentences.** The API returns `freshness` codes; the text lives in
  `src/messages/{pl,en}/wiki.json` and `review.json`.

### API

| Endpoint | Access | Purpose |
|---|---|---|
| `GET /wiki/articles`, `GET /wiki/articles/{id}` | member | Add `freshness` and `review_due_at`. |
| `GET /reviews/overdue` | expert + MFA | Paged overdue list, oldest first, with `is_own`. |
| `POST /reviews/{article_id}/reconfirm` | expert + MFA | R3. |
| `GET/PUT /admin/wiki-settings` | `elevated_router(Role.ADMIN)` | Default period, hourly limit, audit. |

## Chunks

- **15.1 Data and freshness.** Migration, models, `wiki/freshness.py`, settings service. *Tests:*
  unit (boundaries, precedence), migration and CHECK tests, backfill, table registration.
- **15.2 Governance.** `decide` sets the date, `reconfirm` service and endpoint, audit events.
  *Tests:* integration (quorum, per-article period, DISPUTED path, author refused, version conflict,
  index operations unchanged, audit without content, honeypots excluded).
- **15.3 Reads and citations.** `freshness` in wiki responses, `review_overdue` in citations,
  overdue list, admin settings endpoint, `npm run gen:api`. *Tests:* integration; a test that overdue
  articles are still retrieved; no change to prompt versions.
- **15.4 Frontend.** Badge in `WikiList`, `ArticleView` and chat citations; „Do przeglądu” tab in
  `/review` with the confirm action; period field in `DecisionForm`; default period in admin. All
  text in both catalogues; layout at 360 px. *Tests:* Vitest with msw, in both languages.
- **15.5 E2E and closure.** E2E scenario 17, `SECURITY_REVIEW.md` section, *Implementation*,
  *Changes against the plan*, *Tests*.

## Risks and notes

- **A reminder is needed to make this work.** Without Stage 17 the list is only seen by experts who
  open it. Stage 15 is still useful alone, because members see the warning.
- **Warning fatigue.** If the council is small, many articles could show the warning at once, and
  members may start to ignore it. The 12-month default is a starting point; adjust it from real
  use. Confirming many articles at once is left out on purpose, so each one is actually looked at.
- **The date says nothing about accuracy.** „Nadal aktualny” records that an expert looked. The
  audit log and the verification date show who and when, nothing more.
