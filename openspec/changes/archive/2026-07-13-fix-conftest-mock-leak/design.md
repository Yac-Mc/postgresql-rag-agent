# Design: Fix conftest.py Mock Leak Into Integration Tests

## Context

`tests/unit_tests/conftest.py` applies 8 module-level patches so that
`agent.graph`'s import-time `setup_graph()` runs fully offline. Only 3 of
those 8 (`_bootstrap_patch`, `_neo4j_patch`, `_postgres_verify_patch`) keep a
reference to their `Patcher` object; the other 5
(`psycopg2.connect`, `sqlalchemy.create_engine`, `sqlalchemy.inspect`,
`GraphDatabase.driver`, `SentenceTransformer`) are started and discarded, so
they can never be stopped. With `testpaths = ["tests"]` running unit and
integration tests in one process, those 5 mocks stay active for the whole
process and leak into `tests/integration_tests/test_graph.py`, which then
hits mocks instead of the real Postgres/Neo4j connection.

## Goals / Non-Goals

**Goals**
- Track all 8 patches in a single `_active_patches` list
- Stop all of them deterministically after the unit test package finishes,
  before integration tests run
- Keep the unit suite's behavior and assertions unchanged

**Non-Goals**
- Any change to `src/agent/*` or other production modules
- Splitting `testpaths` into separate pytest invocations
- Adding new test coverage beyond the leak fix

## Decisions

### Decision: Use `mock.patch.stopall()` instead of manually iterating `.stop()`

**Choice**: The teardown fixture calls `unittest.mock.patch.stopall()`
instead of `for p in _active_patches: p.stop()`.

**Rationale**: `patch.stopall()` is the stdlib-provided, idempotent
mechanism for this exact case. It stops every currently-active patch
started via `patch(...).start()`/`patch.object(...).start()` in the process,
in reverse start order, and **silently skips patches that were already
stopped** (e.g. by a test's own local `patch` cleanup) instead of raising
`RuntimeError`. Manually iterating `_active_patches` and calling `.stop()`
on each would require our own try/except around every call to get the same
safety, duplicating logic `stopall()` already provides correctly.

**Alternative considered**: `for p in _active_patches: p.stop()` wrapped in
`try/except RuntimeError: pass` per entry — rejected. It works, but
`stopall()` is the standard tool for "stop everything I started", requires
no extra guard code, and removes a footgun (double-stop) that hand-rolled
iteration would need to defend against explicitly.

**Trade-off accepted**: `stopall()` stops *all* active patches in the
process, not only the 8 tracked in `_active_patches`. In this codebase that
scope is correct — `_active_patches` is intended to represent every
module-level patch this conftest starts, and no other conftest in the repo
starts unstoppable module-level patches. We still keep `_active_patches` as
the explicit list (for the "all 8 patches restored" test scenario and
documentation clarity), but the teardown fixture calls `stopall()` rather
than iterating that list by hand.

### Decision: Teardown via a `scope="package", autouse=True` fixture, not `atexit` or manual `.stop()` calls

**Choice**: Add one fixture in `tests/unit_tests/conftest.py`:

```python
import pytest
from unittest.mock import patch

@pytest.fixture(scope="package", autouse=True)
def _stop_module_patches():
    yield
    patch.stopall()
```

**Rationale**: `scope="package"` ties teardown to the end of collection for
`tests/unit_tests/` (the package this conftest belongs to), which runs
before `tests/integration_tests/` in the same process when both are
collected together. `autouse=True` means no test file needs to opt in.

**Alternative considered**: registering an `atexit` hook — rejected, fires
only at interpreter exit, too late to protect integration tests running in
the same process. Stopping patches directly under the module-level code
(no fixture) — rejected, there is no reliable teardown hook at plain import
time; a fixture is the correct pytest primitive for setup/teardown pairing.

## Implementation Notes

1. In `tests/unit_tests/conftest.py`, keep the existing 8 `.start()` calls
   as-is, but append each returned `Patcher` object to a module-level
   `_active_patches = []` list right after `.start()` (including the 3 that
   already keep a named variable — the named variables stay for
   readability, they just also get appended to the list).
2. Add the `_stop_module_patches` fixture shown above at the bottom of the
   file, after all `.start()` calls.
3. No changes to `src/agent/*` or any production import path.

## Verification Plan

- **Unit suite unaffected**:
  `pytest -m "not integration" -q`
  Expect: same pass count and same mocked call-count assertions as before
  this change (no regressions from adding `_active_patches` tracking or the
  teardown fixture).

- **Full suite, mocks do not leak** (requires `postgres-local` and
  `neo4j-local` Docker containers running):
  `pytest -q`
  Expect: `tests/integration_tests/test_graph.py`'s 2 tests pass using the
  real `psycopg2.connect` / `GraphDatabase.driver`, not the leaked mock —
  confirmable by asserting via `psycopg2.connect is <original function>`
  (or equivalent) is restored, or simply that integration tests fail loudly
  if Docker containers are down (proving they're hitting the real network,
  not a mock that would silently succeed offline).

- **Regression check for double-stop safety**: run the full suite twice in
  a row in the same session/process if the local runner supports it, or add
  a one-off assertion in a scratch test that calling `patch.stopall()` a
  second time (e.g. from a test's own local `patch` context manager exiting
  after the fixture teardown already ran) does not raise.

## Risks / Trade-offs

| Risk | Mitigation |
|------|------------|
| `stopall()` also stops patches from other conftests/tests active in the same process | No other conftest in this repo starts unstoppable module-level patches; confirmed by grep across `tests/` |
| `scope="package"` teardown timing depends on pytest's package boundary detection | Verified via full-suite run with Docker containers up before merging |

## Migration Plan

None — test-infrastructure-only change, single file, no runtime or data
migration.

## Open Questions

None.
