# Proposal: Add Real Test Coverage

## Intent

`tests/unit_tests/test_configuration.py` and `tests/integration_tests/test_graph.py`
are LangGraph starter-template placeholders (`{"changeme": ...}`, trivial
passthrough) — zero real coverage. `src/agent/graph.py` (~1750 lines) and
`db_bootstrap.py` hold untested pure logic (SQL-to-AST parsing, dangerous-keyword
validation, dynamic DDL introspection) plus two previously-hand-verified bugs
(missing `DATABASE_URL` → `ValueError`, hardcoded `sslmode` breaking local
Postgres) with no regression guard. Gemini free quota has already been
exhausted multiple times today — tests MUST NOT call `ChatGoogleGenerativeAI`.

## Scope

### In Scope
- `pytest-cov` + coverage config (no enforced threshold — informational only)
- Unit tests for pure logic: `SQLProcessor.validate_sql`, `SQLProcessor.parse_sql_to_ast`,
  `obtener_ddl_dinamico` (with a mocked SQLAlchemy inspector)
- Unit tests for `analizar_seguridad` dangerous-keyword detection with Gemini
  call mocked (`ChatGoogleGenerativeAI` never invoked)
- Regression tests: `ChatbotConfig` raises `ValueError` when `DATABASE_URL` is
  unset; `sslmode` is never hardcoded into the connection string
- Unit tests for `db_bootstrap._parse_connection` (pure, no I/O)
- A small opt-in integration suite (`tests/integration_tests/`, marked
  `@pytest.mark.integration`, skipped by default) against real local
  Postgres/Neo4j Docker containers for `db_bootstrap.ensure_app_database`
  idempotency and `SQLProcessor.execute_sql` against a real connection
- Replace placeholder test files with real content

### Out of Scope
- 100% coverage or enforced coverage gate (thesis/personal project)
- Mutation testing, property-based testing (Hypothesis)
- Testing `LangGraphAgent._build_graph` end-to-end (would require mocking
  the entire LangGraph state machine — low value vs. effort)
- Testing `FastAPI` endpoints in `api.py` (no dedicated app logic beyond
  wiring; deferred)
- CI pipeline setup (roadmap item, separate change)

## Capabilities

### New Capabilities
- `test-coverage`: automated unit + opt-in integration test suite for the
  agent's pure logic and documented regression cases

### Modified Capabilities
None

## Approach

1. Add `pytest-cov` and `pytest-mock` to `dependency-groups.dev` in `pyproject.toml`.
2. Add `[tool.pytest.ini_options]` with `markers = ["integration: requires local Docker Postgres/Neo4j"]`
   so `pytest -m "not integration"` runs fast/offline by default.
3. Unit tests live in `tests/unit_tests/`, mock all external I/O (DB engine,
   Gemini client, Neo4j driver) using `unittest.mock`/`pytest-mock`.
4. Integration tests live in `tests/integration_tests/`, gated by the
   `integration` marker, connect to `postgres-local`/`neo4j-local` containers
   already running locally — no test containers/fixtures spun up by the suite.
5. No coverage threshold enforced in CI/config; `pytest-cov` report is
   informational (`--cov=src/agent --cov-report=term-missing`).

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `pyproject.toml` | Modified | Add pytest-cov, pytest-mock, pytest markers config |
| `tests/unit_tests/test_configuration.py` | Modified | Replace placeholder with `DATABASE_URL`/config regression tests |
| `tests/unit_tests/test_sql_processor.py` | New | `validate_sql`, `parse_sql_to_ast` tests |
| `tests/unit_tests/test_db_bootstrap.py` | New | `_parse_connection` tests |
| `tests/unit_tests/test_security_analysis.py` | New | Dangerous-keyword detection, Gemini mocked |
| `tests/integration_tests/test_graph.py` | Modified | Replace placeholder with opt-in real-Docker tests |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Integration tests silently call real Gemini API | Low | Mock at import boundary; assert call count == 0 in unit tests |
| Integration tests fail in environments without Docker running | Med | `integration` marker excluded by default; documented in test README |
| Over-testing trivial code, wasting thesis time | Low | Scope capped to pure logic + 2 documented regressions only |

## Rollback Plan

New test files and `pyproject.toml` additions only — no production code
behavior changes. Revert by removing the new test files and the
`pytest-cov`/`pytest-mock`/marker additions in `pyproject.toml`.

## Dependencies

- Local Docker containers `postgres-local` and `neo4j-local` running (for
  the opt-in integration suite only; unit suite has no dependency)

## Success Criteria

- [ ] `pytest -m "not integration"` runs offline, no network/Gemini calls, all green
- [ ] `validate_sql`, `parse_sql_to_ast`, `obtener_ddl_dinamico`, `_parse_connection` covered
- [ ] Regression test exists for missing `DATABASE_URL` → `ValueError`
- [ ] Regression test exists confirming no hardcoded `sslmode` in connection string
- [ ] Opt-in integration suite passes against local Docker containers
