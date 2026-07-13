# Tasks: Fix conftest.py Mock Leak Into Integration Tests

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~15-20 (single file, add list + fixture) |
| 400-line budget risk | Low |
| Chained PRs recommended | No |
| Suggested split | Single PR |
| Delivery strategy | single-pr |
| Chain strategy | pending |

Decision needed before apply: Yes
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Low

### Suggested Work Units

| Unit | Goal | Likely PR | Notes |
|------|------|-----------|-------|
| 1 | Track all 8 patches + add teardown fixture + verify both suites | PR 1 | Single-file change, includes manual verification before close |

## Phase 1: Implementation

- [x] 1.1 In `tests/unit_tests/conftest.py`, add `_active_patches = []` at module level (after imports, before the first `.start()` call at line 29).
- [x] 1.2 Append the return value of each `.start()` call to `_active_patches`: the 5 currently-discarded patches (`psycopg2.connect`, `sqlalchemy.create_engine`, `sqlalchemy.inspect`, `GraphDatabase.driver`, `SentenceTransformer`, lines 29-46) and the 3 already-named patches (`_bootstrap_patch`, `_neo4j_patch`, `_postgres_verify_patch`, lines 52-62) — keep the named variables, just also append them to the list.
- [x] 1.3 Add `import pytest` at the top of `tests/unit_tests/conftest.py`.
- [x] 1.4 Add the `_stop_module_patches` fixture at the bottom of the file: `@pytest.fixture(scope="package", autouse=True)`, body is `yield` then `patch.stopall()` (per design.md — do not manually iterate `_active_patches` for the stop call).

## Phase 2: Manual Verification (required before closing the change)

- [x] 2.1 Run `pytest -m "not integration" -q` and confirm it stays green with the same pass count and same mocked call-count assertions as before this change (no regressions from `_active_patches` or the new fixture). **Result: 17 passed, 2 deselected, 0 failures.**
- [x] 2.2 Start local Docker containers `postgres-local` and `neo4j-local`. **Both were already up (16h uptime).**
- [x] 2.3 Run the full suite `pytest -q` (unit + integration in one process) and confirm both tests in `tests/integration_tests/test_graph.py` hit the real Postgres/Neo4j connection instead of the leaked mock. **Result: 18 passed, 1 failed.** `test_ensure_app_database_is_idempotent` passes against the real connection. `test_execute_sql_against_real_connection` now reaches the real connection too (previously it silently passed against the leaked mock) and surfaces a genuine, pre-existing, unrelated bug: `argument 2 must be a connection, cursor or None` in `SQLProcessor.execute_sql` (`src/agent/sql_processing.py:140`, `conn.execution_options(stream_results=True)`), masked until now by the mock leak. This is out of scope for this change (test-infra only) and is logged as a new backlog item, not fixed here.
- [x] 2.4 Record the actual command output (pass/fail counts) for both runs in the PR description as evidence, not just "tests pass". Done — see 2.1/2.3 above.

## Phase 3: Cleanup

- [x] 3.1 Confirm no changes were introduced outside `tests/unit_tests/conftest.py` (`git diff --stat`).
- [x] 3.2 Update the change's proposal/spec/design status per project convention if applicable (e.g. mark success criteria checkboxes in `proposal.md`).
