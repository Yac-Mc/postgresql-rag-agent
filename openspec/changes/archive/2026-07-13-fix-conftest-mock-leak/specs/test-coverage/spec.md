# Delta for test-coverage

## MODIFIED Requirements

### Requirement: Offline Unit Suite Runs Fast and Green

The system MUST provide a unit test suite that runs via
`pytest -m "not integration"` without any network calls, without invoking
`ChatGoogleGenerativeAI`, and without requiring Docker containers.
(Previously: no explicit requirement that unit-suite mocks be torn down
before other test packages run in the same process.)

#### Scenario: Running the offline suite

- GIVEN no Docker containers running and no `GOOGLE_API_KEY` configured
- WHEN a developer runs `pytest -m "not integration"`
- THEN all unit tests pass
- AND no test raises a network or authentication error

#### Scenario: Gemini client never invoked in unit tests

- GIVEN `ChatGoogleGenerativeAI` is mocked at the import boundary
- WHEN unit tests covering `analizar_seguridad` execute
- THEN the mock's call count is asserted to be exactly 0 for dangerous-keyword
  cases
- AND asserted to be exactly 1 (mocked, not real) for safe-query cases that
  reach the LLM branch

#### Scenario: Full suite run does not change unit test behavior

- GIVEN `tests/unit_tests/conftest.py` tracks all 8 module-level patches in
  `_active_patches` with a package-scoped autouse teardown fixture
- WHEN a developer runs `pytest -m "not integration"` after this change
- THEN the unit suite passes with the same outcomes as before the change
- AND no unit test's assertions or mocked call counts differ from the
  pre-change baseline

## ADDED Requirements

### Requirement: Unit-Suite Mocks Do Not Leak Into Other Test Packages

The system MUST stop every patch applied by `tests/unit_tests/conftest.py`
before any other test package (e.g. `tests/integration_tests/`) executes in
the same pytest process. All patches started via `patch(...).start()` or
`patch.object(...).start()` in that conftest MUST be appended to a
module-level `_active_patches` list, and a `scope="package", autouse=True`
fixture in the same conftest MUST call `.stop()` on every entry in
`_active_patches` after `yield`.

#### Scenario: Integration tests hit the real connection after full-suite run

- GIVEN `postgres-local` and `neo4j-local` Docker containers are running
- AND a developer runs the full suite (`tests/unit_tests/` and
  `tests/integration_tests/`) in one pytest process
- WHEN pytest finishes collecting and tearing down `tests/unit_tests/`
- AND `tests/integration_tests/test_graph.py` executes its 2 tests
- THEN both tests call the real `psycopg2.connect` / Neo4j `GraphDatabase.driver`
  connection, not a leaked mock
- AND both tests pass

#### Scenario: All 8 patches are restored after the unit package teardown

- GIVEN the package-scoped autouse teardown fixture in
  `tests/unit_tests/conftest.py` has run after the last unit test
- WHEN `psycopg2.connect`, `sqlalchemy.create_engine`, `sqlalchemy.inspect`,
  `GraphDatabase.driver`, and `SentenceTransformer` are inspected
- THEN each one is the original, unpatched function or class
- AND none of them still returns a `MagicMock` or mocked object

#### Scenario: Stopping patches twice does not raise

- GIVEN `_active_patches` contains each of the 8 patches exactly once
- WHEN the package-scoped teardown fixture calls `.stop()` on every entry
- THEN no `RuntimeError` is raised for an already-stopped patch
- AND the fixture completes without error even if a unit test file also
  used its own function-scoped `patch` for a subset of these targets
