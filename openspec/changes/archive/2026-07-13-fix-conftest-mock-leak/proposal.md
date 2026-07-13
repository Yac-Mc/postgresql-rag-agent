# Proposal: Fix conftest.py Mock Leak Into Integration Tests

## Intent

`tests/unit_tests/conftest.py` applies 8 module-level patches
(`patch(...).start()` / `patch.object(...).start()`) to mock Postgres,
SQLAlchemy, Neo4j, and sentence-transformers before `agent.graph` is
imported, since `setup_graph()` runs at import time. Only 3 of those 8
patches keep a reference to the Patcher object (`_bootstrap_patch`,
`_neo4j_patch`, `_postgres_verify_patch`); the other 5
(`psycopg2.connect`, `sqlalchemy.create_engine`, `sqlalchemy.inspect`,
`GraphDatabase.driver`, `SentenceTransformer`) are discarded without
saving a reference, so they can never be stopped (`.stop()`).

`pyproject.toml` has `testpaths = ["tests"]` — a single test tree.
`tests/integration_tests/test_graph.py` calls `psycopg2.connect(...)`
and `create_engine(...)` at runtime inside test functions. When the
full suite runs in the same pytest process (unit + integration), the
5 unreferenced mocks from `unit_tests/conftest.py` stay active forever
and leak into integration tests, which then hit mocks instead of the
real Postgres/Neo4j connection — producing false results only when run
together with the full unit suite (isolated runs pass).

## Scope

### In Scope
- Refactor `tests/unit_tests/conftest.py` so all 8 patches store their
  Patcher object in a single `_active_patches` list
- Add a `scope="package", autouse=True` fixture in the same
  `conftest.py` whose teardown (after `yield`) calls `.stop()` on every
  patch in `_active_patches`
- Verify the fixture teardown timing: `scope="package"` ties teardown
  to the end of `tests/unit_tests/` (the conftest's own package),
  before `tests/integration_tests/` runs in the same process

### Out of Scope
- Any change to `src/agent/*` or other production modules
- Splitting `testpaths` into separate pytest invocations per test tree
  (would also fix the symptom, but changes CI/test-runner behavior
  instead of the mock lifecycle bug itself)
- Adding new test coverage beyond what already exists

## Capabilities

### New Capabilities
None

### Modified Capabilities
- `test-coverage`: fixes the mock teardown behavior in
  `tests/unit_tests/conftest.py` so patches applied for the unit suite
  no longer leak into `tests/integration_tests/` when both run in one
  pytest process

## Approach

1. In `tests/unit_tests/conftest.py`, change every `patch(...).start()`
   / `patch.object(...).start()` call to append its returned Patcher
   object to a module-level `_active_patches` list (including the 3
   that already keep a named reference — those get added to the list
   too, on top of or replacing the individual named variables).
2. Add one `autouse=True` fixture with `scope="package"` that does
   nothing before `yield` and, after `yield`, iterates
   `_active_patches` calling `.stop()` on each.
3. No changes to `src/agent/*` or any production import path — the fix
   is fully contained in test infrastructure.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `tests/unit_tests/conftest.py` | Modified | All 8 patches tracked in `_active_patches`; new package-scoped autouse fixture stops them after the unit suite finishes |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| `scope="package"` teardown fires later than expected if test collection order changes | Low | Confirmed via full-suite run with local Postgres/Neo4j containers up, asserting integration tests hit the real connection |
| Stopping an already-stopped patch raises `RuntimeError` | Low | Only patches added via `_active_patches` are stopped, each exactly once, in the single teardown fixture |

## Rollback Plan

Single-file change in test infrastructure only. Revert
`tests/unit_tests/conftest.py` to its previous state; no production
code or config is touched.

## Dependencies

- Local Docker containers `postgres-local` and `neo4j-local` running
  (only needed to verify the full-suite scenario; not needed for
  `pytest -m "not integration"`)

## Success Criteria

- [x] `pytest -m "not integration"` stays green with no behavior change
      (17 passed, 2 deselected, 0 failures).
- [x] Full suite (unit + integration in one process, with
      `postgres-local`/`neo4j-local` up) makes both integration tests
      hit the real Postgres/Neo4j connection instead of the leaked mock.
      Confirmed: `test_ensure_app_database_is_idempotent` passes against
      the real connection; `test_execute_sql_against_real_connection`
      now also reaches the real connection (previously silently passed
      against the leaked mock) and surfaces a genuine, pre-existing,
      unrelated bug in `SQLProcessor.execute_sql` (`stream_results`
      incompatibility with psycopg2), masked until now by the mock
      leak. That bug is out of scope for this change and is tracked as
      a new backlog item.
