# Tasks: Add Test Coverage

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~350-450 |
| 400-line budget risk | Medium |
| Chained PRs recommended | No |
| Suggested split | Single PR |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending |

Decision needed before apply: Yes
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Medium

### Suggested Work Units

| Unit | Goal | Likely PR | Notes |
|------|------|-----------|-------|
| 1 | Full test-coverage suite (config + conftest + unit tests + integration + verification) | PR 1 | Test-only change, no production code touched; single PR acceptable given cohesive scope |

## Phase 1: Configuration

- [x] 1.1 Edit `pyproject.toml`: add `pytest-cov>=6.0.0` to `[dependency-groups].dev`
- [x] 1.2 Edit `pyproject.toml`: add `[tool.pytest.ini_options]` with `markers = ["integration: requires local Docker Postgres/Neo4j (excluded by default; run with -m integration)"]`, `addopts = "--cov=src/agent --cov-report=term-missing"`, `testpaths = ["tests"]`

## Phase 2: Shared conftest.py

- [x] 2.1 Create `tests/unit_tests/conftest.py` with module-level (not fixture) code: `os.environ.setdefault(...)` for `DATABASE_URL`, `NEO4J_PASSWORD`, `GEMINI_API_KEY`
- [x] 2.2 In same file, add module-level `patch("agent.db_bootstrap.ensure_app_database").start()`, `patch("agent.graph.Neo4jGraph").start()`, `patch("agent.graph.ChatbotConfig.verificar_conexion_postgresql", return_value=True).start()` — no `.stop()` calls, exactly as in design.md

## Phase 3: Unit Tests

- [x] 3.1 Replace placeholder in `tests/unit_tests/test_configuration.py`: test missing `DATABASE_URL` raises `ValueError`
- [x] 3.2 In `test_configuration.py`: regression test asserting `sslmode` is never hardcoded in the connection string
- [x] 3.3 Create `tests/unit_tests/test_sql_processor.py`: tests for `validate_sql` (valid/invalid SQL)
- [x] 3.4 In `test_sql_processor.py`: tests for `parse_sql_to_ast`
- [x] 3.5 In `test_sql_processor.py`: test `obtener_ddl_dinamico` with `patch("agent.graph.inspect", return_value=mock_inspector)`, asserting table/column names appear in output
- [x] 3.6 Create `tests/unit_tests/test_db_bootstrap.py`: tests for `_parse_connection`
- [x] 3.7 Create `tests/unit_tests/test_security_analysis.py`: test dangerous-keyword input short-circuits before `ChatGoogleGenerativeAI` is called (`mock_class.assert_not_called()`)
- [x] 3.8 In `test_security_analysis.py`: test safe-path flow with `patch("agent.graph.ChatGoogleGenerativeAI", return_value=mock_llm_instance)`, asserting `mock_llm_instance.invoke.assert_called_once()` and correct state parsing from mocked `.content`

## Phase 4: Integration Tests (optional, opt-in)

- [x] 4.1 Replace placeholder in `tests/integration_tests/test_graph.py`: mark tests `@pytest.mark.integration`
- [x] 4.2 Add test for `db_bootstrap.ensure_app_database` idempotency against real local Postgres
- [x] 4.3 Add test for `SQLProcessor.execute_sql` against a real connection

## Phase 5: Verification

- [x] 5.1 Run `pytest -m "not integration"` and confirm all pass offline, no network calls
- [x] 5.2 Confirm no test triggers a real `ChatGoogleGenerativeAI` call — verify via `mock.assert_called`/`assert_not_called` assertions already in 3.7/3.8
- [x] 5.3 (Optional) Run `pytest -m integration` against local Docker (postgres-local, neo4j-local running)
- [x] 5.4 Confirm normal end-to-end flow (`langgraph dev` or direct `import agent.graph`) still works with no regressions after these changes
