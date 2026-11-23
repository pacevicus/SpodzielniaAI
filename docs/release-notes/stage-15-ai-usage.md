# Release notes: AI usage monitoring (stage 15, US-09)

Branch `feat/web-ui-modern-platform`. Commits: `aee7eee` (Next.js pin), then the stage-15
commit listed in the release.

## Highlights

- **See AI usage.** Every LLM call is counted for the user it was made for: tokens, calls, and an
  estimated cost. Calls with no user, such as scheduled jobs, are counted as system work.
- **Members** open **Zużycie AI** (`/me/usage`) to see their own totals and their split by task
  for any period up to a year (default: the last 30 days).
- **Administrators** open **Zużycie AI (admin)** (`/admin/usage`, ADMIN with MFA) to see every
  member, the system's own work, the totals, and the split by task.
- **Model prices.** An administrator sets the price of one million prompt and completion tokens on
  each model in **Modele AI**. Unpriced models show tokens only. Prices take effect when the
  configuration revision that holds them is activated.
- **Groundwork for a usage fee.** Counts are kept per day, user, task and model, so a fee can be
  added later without changing what is stored. No billing exists yet.

## Privacy

- Only counters are stored: no prompts, answers, audio or images.
- A member sees only their own figures. The administrator view needs the ADMIN role and MFA.
- Deleting a user deletes their counters.

## Upgrade notes

1. **Migrations.** Apply `0029_ai_usage` with `make migrate`. It adds two price columns to
   `llm_model` and creates `llm_usage_daily`. It is additive; nothing existing is rewritten.
2. **Configuration.** Optional: `SPOLDZIELNIA_LLM__USAGE_CURRENCY` sets the currency label of the
   estimates (default `PLN`, a three-letter code).
3. **Frontend dependency.** Next.js is pinned to `16.4.5` (exact), with `eslint-config-next` to
   match. The previous `16.4.2` is affected by advisories fixed in 16.4.4. The production build
   fails on 16.4.2.
4. **Model prices.** Existing models have no price until one is set. Models configured in `.env`
   cannot be priced, because `.env` has no price fields.

## Known limitations

- Counts for speech recognition and image embeddings record calls, not tokens, because those
  endpoints report no token counts.
- Usage is written after each answer. If the database is unavailable at that moment, the call is
  not counted. The answer itself is not affected.
- The cost is an estimate from the configured price, not the provider's invoice.
- Days are counted in UTC.
- Usage counters are not protected by row-level security. Access is enforced in the API: members
  see only their own rows, and the administrator view needs ADMIN with MFA.

## Verification

| Check | Result |
|---|---|
| Backend unit tests | 1060 passed |
| Backend integration tests | 1478 passed, 3 skipped, 1 failed: the stack smoke script's model checks, because no local model server was reachable on the verification machine |
| Usage integration tests | 8 passed |
| Frontend lint, typecheck, Vitest | clean; 1623 tests passed |
| Frontend production build (Next.js 16.4.5) | passes |
| E2E: usage scenario (`17-ai-usage`) | 3 passed (desktop and phone) |
| E2E: regression (`9`, `14`, `16`) | 23 passed, 24 skipped by project scope |

Not yet run: E2E scenarios that depend on a model server, and the model price dialog in
`/admin/llm` end to end (covered by Vitest only).

## Status

US-09 is IN PROGRESS. Its plan chunks (15.1–15.5) remain unticked in `PLAN.md` until the smoke
script's model checks pass on a machine with a model server.
