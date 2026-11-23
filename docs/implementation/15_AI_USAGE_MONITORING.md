# Stage 15 — AI usage monitoring (US-09)

> **Story ([US-09](../USER_STORIES_LOG.md#us-09)):** as an administrator and as a user I want to
> see how much LLM compute was used, by each user and by the whole system, so that a usage fee
> can be introduced later to pay for AI compute. The monitoring must be extendable to fees.

## Refined requirement

Refinement decisions (accepted 2026-11-23):

| Topic | Decision |
|---|---|
| What is measured | **Tokens** (prompt, completion, total) per LLM call, **plus an estimated cost** computed from a price per 1M tokens set per model in `/admin/llm`. The gateway already returns token counts (`llm/gateway.py`, `_usage`); this stage persists them. A model without a price shows tokens only, with no estimate. |
| Who sees what | **A user** sees their own totals (tokens and estimated cost, per period) on their account page. **The ADMIN panel** (role + MFA) shows a table per user, system totals, and filters by period. Users never see other users' numbers. |
| Which calls count | **Every LLM call**: chat, notebook, embeddings, OCR, image recognition, PII and injection checks, background jobs. Each row is tagged with its **purpose** (the existing `/admin/llm` purpose), so a fee can later be set per feature. Calls with no user (background jobs) are recorded under "system". |
| Out of scope | **Billing**: invoices, payments, quotas, limits, and the fee itself. This stage only collects the data a fee needs. |
| Privacy | **Counters only.** No prompt, answer, note text, or file bytes are stored. Consistent with the audit rule "without content". |
| Retention | Daily aggregates per user, purpose and model (see R2). Kept while the account exists. *Assumption, to confirm:* no automatic deletion in this stage. |

Open items for later (not blocking): the currency of the estimate (PLN or USD), and whether a
price change applies to past usage (recommended: past rows keep the price valid at call time).

## Acceptance criteria

```gherkin
Background: member1 and admin1 exist; the model "cloud-chat" has a price of 2.00 per 1M tokens

Scenario: a user sees their own usage
  Given member1 asked three questions in the chat this month
  When member1 opens "Twoje dane" → "Zużycie AI"
  Then the page shows the tokens and the estimated cost for the current month
  And the numbers equal the sum of member1's calls for that period
  And no other user's numbers are shown

Scenario: the administrator sees all users and the system total
  Given member1 and member2 both used the chat
  When admin1 (with MFA) opens "Administracja" → "Zużycie AI" for the last 30 days
  Then a table lists every user with tokens and estimated cost
  And a "system" row and a total show the sum of all rows
  And the totals equal the sum of the rows

Scenario: background calls are counted under the system
  Given the embedding job indexed a new article
  When admin1 views the usage for the period
  Then the embedding tokens appear under the purpose "embeddings" in the "system" row

Scenario: a model without a price
  Given the model "local-embed" has no price
  When admin1 views the usage
  Then the tokens are shown, and the cost cell says that no price is set

Scenario: no content is stored
  When a user asks a question in the chat
  Then the database holds only counters (user, day, purpose, model, tokens, cost)
  And no prompt or answer text is written to any usage table

Scenario: a failed usage write does not break the answer
  Given the usage table is temporarily unavailable
  When member1 asks a question
  Then the answer is returned
  And the failure is logged without content
```

## Design

- **R1. One row per call is not stored; daily aggregates are.** The gateway reports the call
  once; the usage service adds the counts to `llm_usage_daily` keyed by
  `(day, user_id or NULL for system, purpose, model)`. A `UPSERT` adds the counts atomically, so
  concurrent calls never lose an increment.
- **R2. Stored columns:** `day`, `user_id` (nullable), `purpose`, `model`, `calls`,
  `prompt_tokens`, `completion_tokens`, `cost_micro` (cost in millionths of the currency unit,
  an integer, to avoid float drift), `priced` (bool: a price existed at call time). No text
  columns other than codes.
- **R3. Prices** live on the model configuration in `/admin/llm` as `price_per_million_in`,
  `price_per_million_out` (non-negative, CHECK in the database). A change creates a new
  configuration revision (existing mechanism, `RevisionOut`), so history is kept.
- **R4. Row-level security:** `llm_usage_daily` has RLS: a user reads only rows with their
  `user_id`. The admin endpoints use the elevated role and read all rows, including "system".
- **R5. Admin endpoints:** `elevated_router(Role.ADMIN)` (role + MFA), CSRF on unsafe methods,
  read-only (no writes, so no audit entry is required; the audit rule applies to changes such as
  price edits, which go through the existing `/admin/llm` audit).
- **R6. Recording never fails the answer.** The usage write runs after the LLM result is
  produced. If it fails, the failure is logged with the code only, and the user still gets the
  answer. *Trade-off:* a missing row undercounts usage. This is accepted for now and is named in
  the risks. Usage is not a security control, so fail open is allowed here (the rule for
  sensitive settings is unchanged).
- **R7. Extendability for fees:** the query layer groups by `purpose`, `model` and `day`, and
  takes a price rule as a function input. A future fee table plugs into the same query without a
  new schema. No fee logic in this stage.
- **R8. Text in the UI, not in code:** every label goes to `src/messages/pl|en/usage.json`
  (Stage 14 rules). Numbers and dates use `useFormatter()` and the named formats.
- **R9. Dependencies:** none new. A chart, if wanted later, uses the existing charting plan; this
  stage shows tables.

## Chunks

- [ ] 15.1 Migration `llm_usage_daily` (RLS, CHECK on counts ≥ 0, unique key), price columns on the model configuration (migration + revision), `test_db_models.py` entries.
- [ ] 15.2 Usage service: `record(user_id, purpose, model, usage, price)` with an atomic upsert; the gateway call site records after the result; unit tests for counting, the system row, unpriced models, and that a write failure does not raise into the answer.
- [ ] 15.3 Price cost computation (integer micro-units) and the user endpoint `GET /me/usage?period=` with RLS tests (user A never sees user B).
- [ ] 15.4 Admin endpoint `GET /admin/usage?from=&to=` (per user, system row, totals, per purpose and model), integration test with two users and one background call.
- [ ] 15.5 Frontend: "Zużycie AI" on the account page (`Twoje dane`) and in the admin console; empty, unpriced and error states; i18n keys in both languages; Vitest for formatting; E2E scenario (member and admin).
- [ ] 15.6 Security review section in `docs/SECURITY_REVIEW.md`, implementation/changes/tests sections in this file, US-09 DONE with commit hashes.

## Risks and notes

- **Undercounting on write failure (R6):** accepted for now. A fee stage would need a
  reconciliation job or an outbox table; decide before a fee is charged.
- **Estimate is not the invoice:** the cost is computed from the configured price, not from the
  provider's bill. The UI must say "estimated".
- **Price edits:** a change applies to future calls only; past rows keep their stored
  `cost_micro`, so history does not move when a price changes.
- **Privacy of per-user numbers:** usage data is personal data of the member. The user view and
  the admin view must both respect RLS; the admin view is limited to ADMIN with MFA.
- **Model in the key:** if a model is renamed, usage rows keep the old name. The model's stable
  ID (not its display name) is stored.
