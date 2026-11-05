# Stage 19 — Engineering quality gates (US-13)

> **Story ([US-13](../USER_STORIES_LOG.md#us-13)):** as a maintainer I want CI to stop quality getting
> worse, so that coverage, file sizes and end-to-end behaviour are checked by machines, without a
> large refactor before the next feature.

This stage changes no product behaviour, so it has no user-facing acceptance scenarios beyond CI
itself. It can be done at any point, and doing it first would protect Stages 15-18.

## Refined requirement

Refinement decisions (accepted 2026-11-05):

| Topic | Decision |
|---|---|
| Approach | **Ratchet from today.** Thresholds start at measured values and can only tighten. Existing debt does not block PRs. |
| Coverage | Backend and frontend minimums, starting at the measured value. Lowering one requires a stated reason in the commit that does it. |
| File size | New source files have at most 500 lines. The 12 files over the limit go on a baseline that can only shrink. Nothing is split in this stage. |
| Test speed | `pytest-xdist` where it is proven to help. |
| E2E | **Nightly and on demand,** not on every PR, because it needs the whole stack. |
| Out of scope | Reaching 90% coverage, splitting the large files, mutation testing, bundle budgets. |

## Acceptance criteria

```gherkin
Scenario: coverage cannot fall
  Given the recorded backend minimum is N percent
  When a PR brings combined unit and integration coverage below N
  Then CI fails and names the measured value and the minimum

Scenario: file size cannot grow
  When a PR adds a new source file over 500 lines
  Then CI fails and names the file
  When a PR makes a file on the baseline longer than its recorded length
  Then CI fails
  When a PR brings a baseline file down
  Then CI fails until the baseline is lowered to match, so the gain is kept

Scenario: nightly E2E
  When the nightly workflow runs, or a maintainer starts it by hand
  Then a throwaway stack starts, Playwright runs, and the report is uploaded
  And a failure shows as a failed run, without blocking any PR
```

## Baseline measured on 2026-11-05

- **Backend, unit tests only:** 60% (1,041 passed, 414 deselected, about 60 s). The value that matters
  is the combined one, which needs the integration job and is measured in 19.1.
- **Frontend:** not yet measurable; no coverage provider is installed.
- **Files over 500 lines:** `llm/config_service.py` 1157, `api/admin_llm.py` 875,
  `components/admin/llm/Catalog.tsx` 797, `llm/gateway.py` 784, `auth/user_admin.py` 698,
  `core/settings.py` 645, `atlas/generations.py` 611, `workers/runtime.py` 608, `vector/reembed.py` 607,
  `atlas/recognition.py` 584, `atlas/submissions.py` 529, `components/admin/users/UserPanel.tsx` 510.

## Design

- **R1. Combined backend coverage.** The `lint-type-unit` and `integration` jobs each upload a coverage
  data file. A new `coverage` job combines them and applies `fail_under` from `[tool.coverage.report]`
  in `pyproject.toml`. `integration` tests in CI have no separate threshold. Code that runs only in
  containers is not measured, so `[tool.coverage.run]` omissions are listed with a reason, not used to
  raise the number.
- **R2. Frontend coverage.** Add `@vitest/coverage-v8` at an exact version, matching the installed
  `vitest` and older than two weeks. Thresholds go in the Vitest configuration; `npm test` stays fast,
  and a new `test:coverage` script runs in CI.
- **R3. Rounding down.** Each minimum is the measured value rounded down to a whole number, so normal
  variation does not fail builds.
- **R4. File size check.** `infra/scripts/check_file_sizes.py` (standard library only) counts lines in
  `src/**/*.py` and `frontend/src/**/*.{ts,tsx}`, excluding migrations, tests and generated
  `api-schema.d.ts`. `infra/file-size-baseline.txt` lists `path length`. Rules follow the scenarios
  above. It runs in CI and in `make check`.
- **R5. xdist with evidence.** `pytest-xdist` is added for the unit suite first. Integration tests start
  their own containers, so workers may multiply startup costs or conflict on shared state. They adopt
  xdist only if a timed comparison shows a real gain with no flakiness over several runs; otherwise this
  is recorded and they stay serial.
- **R6. Nightly workflow.** `.github/workflows/e2e.yml` runs on `schedule` and `workflow_dispatch`,
  with `permissions: contents: read`, actions pinned by commit, and `ubuntu-24.04`. It generates
  throwaway secrets as `dr-rehearsal` does, starts the stack, runs migrations and `make e2e` on
  `chromium`, with `tablet` and `mobile` on the on-demand run. The report is uploaded for 14 days, even
  on failure. Nightly results are not required checks.
- **R7. No model server on the runner.** The runner has no real LLM. Scenarios that need one skip using
  the rule already in scenario 4 (`AGENTS.md` §8). Any new skip must name its reason, and 19.3 lists what
  is left uncovered, so a green run is not mistaken for full coverage.
- **R8. Documentation.** `AGENTS.md` §2 and `CONTRIBUTING.md` describe the new commands, and how to lower
  a baseline. The repository is public, so uploaded reports must contain only generated data.

## Chunks

- **19.1 Backend coverage.** R1, R3, with the real combined figure recorded here. *Tests:* a deliberate
  drop on a scratch branch fails CI.
- **19.2 Frontend coverage.** R2, R3. *Tests:* `npm run test:coverage` locally and in CI.
- **19.3 File size check.** R4, baseline, `make check`. *Tests:* unit tests for each rule, including a
  stale baseline and a removed file.
- **19.4 xdist.** R5, with before-and-after timings for three runs. *Tests:* suites are green, the
  comparison is recorded under *Tests*.
- **19.5 Nightly E2E.** R6, R7, with one successful manual run linked. *Tests:* the workflow itself; the
  list of skipped scenarios recorded here.
- **19.6 Closure.** R8, `SECURITY_REVIEW.md` section (CI permissions, artifact contents), plan sections.

## Risks and notes

- **Coverage is a floor, not a target.** At 60% for unit tests, the number alone says little about
  safety here. Iron-rule tests matter more, and this stage does not replace them.
- **Nightly E2E will likely fail at first.** It has never run in CI, so 19.5 may uncover environment
  problems. Those are fixed or reported honestly with a reason. If it remains flaky, it stays non-blocking
  until it is stable.
- **CI minutes and repository traffic.** A nightly full-stack run costs time. Frequency can be reduced to
  weekly if it becomes a burden.
- **Line limits can be gamed.** A limit is a prompt to split, not proof of good design. Splitting large
  security-sensitive files such as `llm/gateway.py` should be its own reviewed work.
