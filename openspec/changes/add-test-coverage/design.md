# Design: Add Test Coverage

## Context

`src/agent/graph.py` builds `ChatGoogleGenerativeAI` clients inline inside
each node method (`analizar_seguridad`, `generar_sql`, `generar_respuesta`)
via a local `_invocar_sync()` closure — there is no injectable LLM client
attribute to swap. Module-level side effects also run at import time:
importing `src.agent.graph` executes `agent = setup_graph()`, which calls
`_bootstrap_demo_database()` and `agent._init_models()` synchronously. Tests
MUST neutralize this import-time behavior before it touches real
Postgres/Neo4j/Gemini.

## Goals / Non-Goals

- Goal: fast, deterministic, offline unit suite; opt-in integration suite.
- Goal: mock Gemini without disabling the rest of the node's control flow.
- Non-Goal: refactoring `graph.py` to inject an LLM client (out of scope —
  would touch production code beyond "add tests").

## Mocking Library Choice: `unittest.mock` (not `pytest-mock`)

Use `unittest.mock.patch` directly, not the `pytest-mock` `mocker` fixture.

| Aspect | Decision |
|--------|----------|
| Library | `unittest.mock` (stdlib) |
| Rationale | No new runtime dependency; `patch("module.ChatGoogleGenerativeAI")` is sufficient; the proposal listed `pytest-mock` as a dev dependency but it is optional sugar — add it only if a test needs a fixture-style mock cleanup, otherwise skip |
| Pattern | `@patch("agent.graph.ChatGoogleGenerativeAI")` as a decorator or `with patch(...)` context manager, patched at the **import location inside `graph.py`**, not at `langchain_google_genai`'s source module |

Decision: **do not add `pytest-mock` unless a concrete test needs it.**
`unittest.mock` fully covers every mocking need identified in the spec
(Gemini client, SQLAlchemy inspector, `psycopg2.connect`). Keep the dev
dependency surface minimal per the proposal's "thesis project" scope.

## Mocking `ChatGoogleGenerativeAI` Without Breaking Node Flow

Each node builds the client inside a nested sync function called via
`asyncio.to_thread`:

```python
def _invocar_sync():
    llm = ChatGoogleGenerativeAI(model="gemini-flash-latest", google_api_key=API_KEY_GEMINI)
    return llm.invoke([HumanMessage(content=prompt_text)])
resultado = await asyncio.to_thread(_invocar_sync)
```

Because `ChatGoogleGenerativeAI(...)` is resolved via the module-global name
at call time, patching `agent.graph.ChatGoogleGenerativeAI` with a
`MagicMock()` intercepts the constructor call. The mock's return value must
implement `.invoke(...)` returning an object with a `.content` attribute
(string), since the node calls `resultado.content.strip()` immediately after.

```python
mock_llm_instance = MagicMock()
mock_llm_instance.invoke.return_value = SimpleNamespace(
    content='{"es_segura": true, "razon": "ok", "riesgo": "bajo"}'
)
with patch("agent.graph.ChatGoogleGenerativeAI", return_value=mock_llm_instance):
    await processor.analizar_seguridad(state)
mock_llm_instance.invoke.assert_called_once()
```

For dangerous-keyword scenarios, the LLM branch is never reached (early
`return state` before `_invocar_sync` is defined) — assert
`mock_llm_instance.invoke.assert_not_called()` or, simpler, assert the patched
class itself was never called (`mock_class.assert_not_called()`).

## Neutralizing Module-Level Import Side Effects

`import agent.graph` (or `from agent import graph`) triggers
`agent = setup_graph()` at module scope, which calls
`_bootstrap_demo_database()` (real Postgres connection attempt) and
`agent._init_models()` (real Gemini/Chroma/Neo4j init). See the
"conftest.py: neutralize import-time side effects BEFORE collection"
section below for how this is handled — module-level `conftest.py` patches,
not fixtures.

For pure-function tests (`validate_sql`, `parse_sql_to_ast`,
`_parse_connection`): these have no dependency on the module-level `agent`
instance; the `conftest.py` patches are enough to make importing them safe.

For node-method tests (`analizar_seguridad`): instantiate `LangGraphAgent`/
`SQLProcessor` directly in the test rather than relying on the module-level
`agent` singleton, so no real `_init_models()` I/O runs; only call the
specific method under test with a mocked LLM.

### conftest.py: neutralize import-time side effects BEFORE collection

**Critical correction**: an `autouse` fixture is NOT sufficient here. Pytest
fixtures run per-test, at execution time — but `agent.graph`'s module-level
`agent = setup_graph()` runs at **import time**, which happens during
pytest's *collection* phase, before any fixture executes. Any test file that
does `from agent.graph import SQLProcessor` (or similar) triggers the real
bootstrap attempt the moment pytest collects that file, regardless of
fixtures defined anywhere.

The fix: set the environment **at module scope in `conftest.py`** (not
inside a fixture function body). `conftest.py` is imported by pytest before
it collects sibling test files in the same directory, so module-level code
in `conftest.py` runs first and is visible by the time `agent.graph` is
imported.

```python
# tests/unit_tests/conftest.py
import os
from unittest.mock import patch

# Module-level (NOT inside a fixture function): runs when conftest.py itself
# is imported, which pytest does before collecting sibling test files —
# guaranteed to run before `agent.graph`'s import-time setup_graph() fires.
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/testdb")
os.environ.setdefault("NEO4J_PASSWORD", "test")
os.environ.setdefault("GEMINI_API_KEY", "test-key-not-real")

_bootstrap_patch = patch("agent.db_bootstrap.ensure_app_database")
_bootstrap_patch.start()

_neo4j_patch = patch("agent.graph.Neo4jGraph")
_neo4j_patch.start()

_postgres_verify_patch = patch(
    "agent.graph.ChatbotConfig.verificar_conexion_postgresql",
    return_value=True,
)
_postgres_verify_patch.start()
```

These patches start at module import time (before test collection touches
`agent.graph`) and are intentionally never stopped — the process exits when
the pytest run ends, so there is no teardown to manage. This guarantees the
"offline suite" claim actually holds: no test file importing `agent.graph`
can trigger a real Postgres/Neo4j connection attempt, no matter what order
pytest collects files in.

`obtener_ddl_dinamico`, `SQLProcessor.validate_sql`/`parse_sql_to_ast`, and
`db_bootstrap._parse_connection` remain pure and need no additional patching
beyond what's already active from `conftest.py`.

For node-method tests (`analizar_seguridad`): instantiate `LangGraphAgent`/
`SQLProcessor` directly in the test rather than relying on the module-level
`agent` singleton, and patch `ChatGoogleGenerativeAI` per-test as shown above.

## Mocking the SQLAlchemy Inspector for `obtener_ddl_dinamico`

```python
mock_inspector = MagicMock()
mock_inspector.get_table_names.return_value = ["users"]
mock_inspector.get_columns.return_value = [
    {"name": "id", "type": "INTEGER"},
    {"name": "email", "type": "VARCHAR"},
]
with patch("agent.graph.inspect", return_value=mock_inspector):
    ddl = obtener_ddl_dinamico(mock_engine)
assert "users" in ddl
assert "email" in ddl
```

Patch at `agent.graph.inspect` (the name as imported into `graph.py`), not
`sqlalchemy.inspect`, per standard `unittest.mock` patch-where-used rule.

## pytest Configuration (`pyproject.toml`)

Add to `[tool.pytest.ini_options]`:

```toml
[tool.pytest.ini_options]
markers = [
    "integration: requires local Docker Postgres/Neo4j (excluded by default; run with -m integration)",
]
addopts = "--cov=src/agent --cov-report=term-missing"
testpaths = ["tests"]
```

Add to `[dependency-groups].dev`:

```toml
dev = [
    "anyio>=4.7.0",
    "langgraph-cli[inmem]>=0.2.8",
    "mypy>=1.13.0",
    "pytest>=8.3.5",
    "pytest-cov>=6.0.0",
    "ruff>=0.8.2",
]
```

No `--strict-markers` needed given the small scope; add if marker typos
become an issue. No coverage threshold (`--cov-fail-under`) per proposal's
explicit "informational only" success criterion.

## Test File Layout

```
tests/unit_tests/
├── conftest.py                    # module-level patches: no real bootstrap
├── test_configuration.py          # DATABASE_URL ValueError, sslmode regression
├── test_sql_processor.py          # validate_sql, parse_sql_to_ast, obtener_ddl_dinamico
├── test_db_bootstrap.py           # _parse_connection
└── test_security_analysis.py      # analizar_seguridad, Gemini mocked

tests/integration_tests/
└── test_graph.py                  # @pytest.mark.integration, real Docker
```

## Risks

| Risk | Mitigation |
|------|------------|
| Patching wrong import path silently no-ops the mock | Always patch at `agent.graph.<Name>`, verify with `mock.assert_called()` in at least one test per mocked symbol |
| Coverage report clutters CI output | `--cov-report=term-missing` is human-readable; no XML/HTML report needed for local thesis use |

**Resolved during design review**: the original design proposed neutralizing
`setup_graph()`'s import-time side effects via an `autouse` pytest fixture.
This does not work — fixtures run per-test, but the module-level side effect
runs at import/collection time, before any fixture. Corrected to use
module-level `conftest.py` code + `unittest.mock.patch(...).start()` without
`stop()`, which runs before pytest collects sibling test files. See the
"conftest.py: neutralize import-time side effects BEFORE collection" section
above for the corrected approach.
