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

## Implementation

| Chunk | Where |
|---|---|
| 15.1 Schema | Migration `0029_ai_usage` (`db/migrations/versions/0029_ai_usage.py`): `llm_model.price_in_micro` / `price_out_micro` with CHECKs (≥ 0, both or neither), table `llm_usage_daily` with two partial unique indexes (user rows, system rows), `ON DELETE CASCADE` from `app_user`. ORM: `db/models/llm.py` (`LlmUsageDaily`). Offline SQL checked with `alembic upgrade --sql`. |
| 15.2 Metering | `llm/usage.py` (`UsageEvent`, integer `cost_micro`, `UsageRecorder` atomic upsert, `BufferedUsage` for worker threads). `llm/usage_context.py` carries the owner. `llm/gateway.py` `_meter` counts every successful response (chat attempts, embeddings; image embeddings and speech count the call with zero tokens). `auth/deps.py` `current_user` sets the owner per request. `workers/runtime.py` `_counted` sets the job owner (`owner_id`) and flushes the buffer on the job's loop. `main.py` wires the recorder into the API gateway. |
| 15.2 Probes | `llm/probes.py` and `api/admin_llm.py`: the playground and model probes take a usage sink as well (`usage=`), so admin tests of cloud models are counted under the admin. |
| 15.3 Prices and member view | Prices are part of the configuration snapshot (`llm/snapshot.py`, `llm/config_store.py`), set on models by `PriceIn` (`llm/config_service.py`, `api/admin_llm.py` `ModelOut`). `GET /me/usage` (`api/usage.py`, `llm/usage_report.py` `member_usage`). Setting `llm.usage_currency` (default `PLN`). |
| 15.4 Administrator view | `GET /admin/usage` (`elevated_router(Role.ADMIN)`, role + MFA): system row, totals, members sorted by cost, totals by purpose. `llm/usage_report.py` `admin_usage`. |
| 15.5 Frontend | `/me/usage` (`app/me/usage`, `components/usage/UsagePanels.tsx`) and `/admin/usage` (`app/admin/usage`, `RequireRole ADMIN`); navigation entries; home cards; price dialog in the model table (`components/admin/llm/Catalog.tsx`); `lib/usage.ts` hooks and period rules; messages `messages/{pl,en}/usage.json`, price strings in `adminLlm.json` (`prices`), `money` number format. The OpenAPI schema and `api-schema.d.ts` regenerated (`npm run gen:api`). |

## Changes against the plan

- **R4 (RLS on `llm_usage_daily`) not applied.** The table holds counters only, and `llm_model`
  (also global configuration) is not RLS-protected either. Isolation is in the API: `/me/usage`
  filters by the caller's id only, and `/admin/usage` is ADMIN + MFA. Integration tests cover both
  (`test_a_member_sees_only_their_own_usage`, `test_members_and_experts_cannot_read_the_administrator_view`).
- **R3 (a price change creates a new revision) not applied.** A price is saved on the model and
  reaches the runtime with the next activation of a revision (it is frozen in the snapshot, so
  history is kept per revision). Saving a price does not create a draft; the UI says so
  (`prices.saved`, `admin.priceNote`).
- **Owner by context, not by parameter.** Requests use a `ContextVar`; worker jobs use a
  process-wide value for the job (one job at a time per process), because `asyncio.Runner` copies
  its context once and the engines run on other threads. Both are documented in
  `llm/usage_context.py`.
- **Worker writes are buffered.** Vision and speech engines run on their own loops; they queue
  events and the job flushes them on the runner's loop when it ends, even on failure.
- **Speech and image embeddings count calls, not tokens** (their endpoints report none).
- **`.env` deployments have no prices** (the env configuration has no price fields), so they show
  tokens and are counted as unpriced. Only models configured in `/admin/llm` can be priced.
- **Day boundaries are UTC.** A period is inclusive UTC days, at most 366 days.
- **Currency** `PLN` is a default, configurable (environment: `SPOLDZIELNIA_LLM__USAGE_CURRENCY`).
  Still open: whether it is PLN or USD for the organisation.
- **Retention** stays as planned (no automatic deletion). Deleting a user removes their counters
  (`ON DELETE CASCADE`); this was chosen over keeping them, to match the erasure path.

## Tests

Run on 2026-11-23 with Docker Desktop and the dev stack (`make up`, `make migrate`).

| Suite | Result |
|---|---|
| `ruff check`, `ruff format --check`, `pyright` (strict) | clean |
| `pytest tests/unit` | 1060 passed |
| `pytest tests/integration/test_ai_usage.py` | 8 passed |
| `pytest` (full, unit + integration) | 1478 passed, 3 skipped (model not reachable or `make mfa-off`), **1 failed**: `test_stack_smoke.py::test_smoke_script_passes` |
| Smoke script, cause of the failure | The schema check passes (`0029_ai_usage` at head). Three model checks fail: worker-core and the API cannot reach the local model endpoint (`llm:11434`), and the embedding separation check cannot run. LM Studio is not running on this machine (`host.docker.internal:1234` unreachable). Environmental, not a code fault |
| `npm run lint`, `npm run typecheck`, `npx vitest run` | clean; 28 files, 1623 tests |
| `npm run build` | passes on Next.js 16.4.5 (pinned exact, with `eslint-config-next` 16.4.5). The previous 16.4.2 was affected by advisories fixed in 16.4.4 |
| E2E `17-ai-usage` (chromium, mobile) | 3 passed: member view and access rules, administrator view, phone width |
| E2E regression: `9-llm-config`, `14-app-shell`, `16-language` (chromium, mobile, tablet) | 23 passed, 24 skipped by project scope |
| E2E not run | `1`–`8`, `10`–`13`, `15`: model-dependent or outside this change. `4-happy-path` needs the model |

Two defects found by the E2E run and fixed: the admin view dropped the system row when no member
had usage (now always listed); the CSP watcher was installed after the page had loaded (the scenario
reloads first, as scenario 16 does).

The configuration `playwright.config.ts` now includes `17-ai-usage` in the `mobile` project.

Not covered by any run: the model price dialog in `/admin/llm` (unit-tested in Vitest only), and
usage numbers produced by real model calls on the stack (needs a model server).

## Status

Implemented and verified except for the smoke script's model checks, which need a local model
server (see *Tests*). US-09 stays IN PROGRESS. Chunks 15.1–15.5 are verified by their tests, but
the project rule ties ticking to a fully green suite, so they remain unticked until the smoke
failure is resolved or accepted.
