# Stage 18 — Dependencies between articles (US-12)

> **Story ([US-12](../USER_STORIES_LOG.md#us-12)):** as an expert I want to record that an article
> relies on another one, so that when the source is replaced or withdrawn, the articles built on it
> are flagged for review.

**Requires Stage 15** (freshness and „Nadal aktualny”). Stage 17 is optional: with it, the flag also
produces a notification.

## Refined requirement

Refinement decisions (accepted 2026-11-05):

| Topic | Decision |
|---|---|
| Effect of a changed source | **Warn and stay,** as with an overdue review: the article stays CANONICAL and in chat, with a warning and a place on the experts' list. |
| Who adds links | **Experts,** during review or afterwards, not authors. It is a judgement about knowledge. |
| Kinds of link | Only one: `depends_on`. ICS's other relation types and graph explorer are not included. |
| What triggers the flag (default, change on request) | The source becomes SUPERSEDED, DISPUTED, REVISION_REQUIRED or REJECTED (withdrawn). A confirmation of the source or a new approval does not. |
| How far it spreads | **One level.** Only direct dependents are flagged; an expert who reconfirms decides whether to continue. |
| Out of scope | Links that authors or models propose, graph views, automatic re-pointing, and any link meaning „duplicate”: overlapping articles are welcome. |

## Acceptance criteria

```gherkin
Background: expert1 is logged in with MFA; article B depends on canonical article A

Scenario: the source is replaced
  When article A is superseded by article C
  Then article B stays canonical and in chat
  And member1 sees "Źródło uległo zmianie" on B and in its citations
  And B is on the experts' list, above articles that are merely overdue
  And with Stage 17, experts who are not authors of B are notified

Scenario: withdrawal does not reveal who withdrew
  When an author's revocation sends A to REVISION_REQUIRED
  Then B is flagged the same way
  And nothing on B says why A changed or who caused it

Scenario: an expert clears the flag
  When expert1 clicks "Nadal aktualny" on B
  Then the dialog offers to replace the link to A with a link to its successor C
  And the flag is cleared, and the review date moves forward
  And the audit log records the changed link

Scenario: hidden sources stay hidden
  Given member1 cannot open A
  Then B shows the warning without a title or link for A

Scenario: invalid links are refused
  When expert1 links an article to itself, to a hidden article or to a non-canonical article
  Then the API answers 422
```

## Design

### Data (migration `0032_article_dependency`)

- `article_dependency (article_id, depends_on_id, created_by, created_at)`, primary key on both ids,
  both foreign keys ON DELETE CASCADE, CHECK `article_id <> depends_on_id`, and an index on
  `depends_on_id` for the reverse lookup.
- `wiki_article.basis_changed_at timestamptz NULL`.

### Rules

- **R1. One trigger point.** `change_status` is the only way an article leaves CANONICAL, whether by
  supersession, dispute, degradation or withdrawal. When the source leaves CANONICAL, it sets
  `basis_changed_at = now()` on direct CANONICAL dependents in the same transaction, locking in id
  order, as `ReviewService._lock` does, to avoid deadlocks.
- **R2. Freshness gains a value.** `superseded` > `basis_changed` > `review_overdue` > `current`. The
  citation flag from Stage 15 becomes `freshness` in place of `review_overdue` (API regenerated in the
  same chunk). It stays out of the prompt, so answers do not change.
- **R3. Clearing.** Reconfirmation and a new final approval clear `basis_changed_at`. Reconfirmation
  takes an optional `replace_dependencies: [{from, to}]`; `to` must be CANONICAL and visible.
- **R4. Links.** `ReviewIn` gains `depends_on_ids` (at most 20), applied at the final approval.
  `PUT /reviews/{article_id}/dependencies` changes them afterwards (expert + MFA + CSRF). Both record
  `ARTICLE_DEPENDENCIES_CHANGED` with ids only. An expert cannot change links on an article they wrote,
  matching `own_article`.
- **R5. Cycles are allowed.** One-level flagging cannot loop, so no cycle check is needed. A cycle just
  means two articles flag each other once, and reconfirming each clears it.
- **R6. No leaks through links.** Members see only `basis_changed`. `GET /wiki/articles/{id}` lists a
  source only if the reader may open it. Hidden articles can never be sources or dependents, and the
  reason for a change is never shown.
- **R7. Notification (with Stage 17).** Event `article.basis_changed`, sent to experts other than the
  article's authors, with one row per article and change.

## Chunks

- **18.1 Data and trigger.** Migration, model, hook in `change_status`, freshness value. *Tests:* every
  way of leaving CANONICAL, one level only, a cycle, concurrent changes without deadlock, rollback, table
  registration.
- **18.2 API.** Links in review, the dependencies endpoint, reconfirm with replacement, reads with the
  visibility filter, citation change, `gen:api`. *Tests:* integration including 422 cases, `own_article`,
  hidden sources, audit without content.
- **18.3 Notification.** R7, if Stage 17 is in place at that point; otherwise this is recorded under
  *Changes against the plan*. *Tests:* recipient rule, no actor or reason in `params`.
- **18.4 Frontend.** Pick sources in `DecisionForm` and on the overdue tab using article search (Stage
  16, or a plain list if not yet built); replacement offer in the confirm dialog; warning text and source
  links in the article. *Tests:* Vitest in both languages.
- **18.5 E2E and closure.** E2E scenario 20, `SECURITY_REVIEW.md`, plan sections.

## Risks and notes

- **Value depends on experts adding links.** Nothing forces them to, and articles without links
  behave as they do today. This is why the stage is planned after the review dates and is the cheapest
  to drop if the council does not use it.
- **Flags can pile up.** A widely used source being replaced flags all direct dependents at once. They
  appear as one list, and Stage 17 batches the emails.
- **A flag is a signal, not a finding.** A dependent may be perfectly valid after its source changes,
  and that is why this stage warns rather than removes.
- **Consent interplay.** A revoked co-author's block can flag dependents through REVISION_REQUIRED. R6
  keeps this from showing who or why, but a member watching timing closely could still infer that the
  source changed. They could already see this from the source's own status, so nothing new is exposed.
