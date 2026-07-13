# Spec Delta: graph-module-layout

## ADDED Requirements

### Requirement: Public Import Compatibility
`src/agent/graph.py` MUST re-export every symbol currently importable from
it, so that `from agent.graph import X` continues to resolve identically
after the module split, for X in: `ChatbotConfig`, `SQLRAGSystem`,
`Neo4jGraph`, `Neo4jManager`, `SQLProcessor`, `State`,
`obtener_ddl_dinamico`, `LangGraphAgent`, `get_graph` (if present),
`agent`, `graph`.

#### Scenario: Importing a moved class from the orchestrator module
- **GIVEN** `ChatbotConfig` has been moved to `agent/config.py`
- **WHEN** a caller executes `from agent.graph import ChatbotConfig`
- **THEN** the import MUST succeed and MUST return the exact same class
  object as `from agent.config import ChatbotConfig`

#### Scenario: Importing every currently-public symbol after the split
- **GIVEN** the module split is complete (`state.py`, `neo4j_manager.py`,
  `sql_processing.py`, `rag.py`, `config.py`, `graph.py`)
- **WHEN** a caller executes
  `from agent.graph import (ChatbotConfig, SQLRAGSystem, Neo4jGraph, Neo4jManager, SQLProcessor, State, obtener_ddl_dinamico, LangGraphAgent, agent, graph)`
- **THEN** every name MUST resolve without `ImportError`

### Requirement: Mock-Patch Target Compatibility
`agent/graph.py` MUST continue to expose the third-party names that
existing tests patch directly on it (`psycopg2`, `inspect`,
`ChatGoogleGenerativeAI`), even after the code that uses them moves to a
new module, because `unittest.mock.patch` mutates the shared module object
referenced by that name, not a per-file copy.

#### Scenario: Patching psycopg2.connect via agent.graph after SQLRAGSystem moves to rag.py
- **GIVEN** `SQLRAGSystem.get_connection` has moved to `agent/rag.py` and
  calls `psycopg2.connect(...)` there
- **WHEN** a test executes `patch("agent.graph.psycopg2.connect", ...)`
- **THEN** the patched `connect` MUST be observed by `rag.py`'s call,
  because `agent.graph` still imports the `psycopg2` module directly

#### Scenario: Patching sqlalchemy inspect via agent.graph after obtener_ddl_dinamico moves to sql_processing.py
- **GIVEN** `obtener_ddl_dinamico` has moved to `agent/sql_processing.py`
- **WHEN** a test executes `patch("agent.graph.inspect", ...)`
- **THEN** the patched `inspect` MUST be observed by
  `sql_processing.py`'s call, because `agent.graph` still imports
  `inspect` directly

### Requirement: Existing Test Suite Passes Unmodified
The 19 existing tests MUST pass without modification to test logic or
assertions. Only import statements MAY change, and only if a test imports
a symbol from a path other than `agent.graph` (none currently do).

#### Scenario: Running the full non-integration suite after the split
- **GIVEN** the module split described in the proposal is complete
- **WHEN** `pytest -m "not integration"` is executed
- **THEN** all unit tests in `tests/unit_tests/` MUST pass with unchanged
  assertions, including:
  - `tests/unit_tests/test_configuration.py` (`from agent.graph import ChatbotConfig, SQLRAGSystem`; patches `agent.graph.psycopg2.connect`)
  - `tests/unit_tests/test_sql_processor.py` (`from agent.graph import SQLProcessor, obtener_ddl_dinamico`; patches `agent.graph.inspect`)
  - `tests/unit_tests/test_security_analysis.py` (`from agent.graph import LangGraphAgent`; patches `agent.graph.ChatGoogleGenerativeAI`)
  - `tests/unit_tests/test_db_bootstrap.py` (`from agent.db_bootstrap import _parse_connection` — unaffected, no `agent.graph` import)

#### Scenario: Running the opt-in integration test after the split
- **GIVEN** Docker containers `postgres-local` and `neo4j-local` are
  running with a real `DATABASE_URL`
- **WHEN** `pytest -m integration` is executed against
  `tests/integration_tests/test_graph.py`
  (`from agent.graph import ChatbotConfig, SQLProcessor`)
- **THEN** both tests MUST pass unmodified

### Requirement: Consumer Modules Work Without Changes
`api.py`, `__init__.py`, and `langgraph.json` MUST continue to function
without any code changes after the split.

#### Scenario: Starting the API server after the split
- **GIVEN** the module split is complete
- **WHEN** `api.py` is imported/started
- **THEN** it MUST resolve `agent.graph.agent` (or whatever symbol it
  currently consumes) without modification and without import errors

#### Scenario: LangGraph CLI resolves the graph attribute
- **GIVEN** `langgraph.json` points at `agent.graph:graph` (or the
  equivalent current attribute path)
- **WHEN** the LangGraph CLI loads the graph
- **THEN** it MUST resolve the same `graph` object as before the split

### Requirement: Import-Time Side Effects Preserved
`agent = setup_graph()` MUST continue to execute at module-import time
with the same side effects as before the split (real connections to
Postgres, Neo4j, and Gemini), regardless of which module the underlying
classes now live in.

#### Scenario: Importing agent.graph triggers the same bootstrap sequence
- **GIVEN** `agent/graph.py` is the orchestrator after the split
- **WHEN** `import agent.graph` executes
- **THEN** `setup_graph()` MUST run and produce the module-level `agent`
  object with the same Postgres/Neo4j/Gemini connections established as
  in the pre-refactor version

### Requirement: No Circular Import Between config.py and neo4j_manager.py
`agent/neo4j_manager.py` MUST NOT perform a real (eager) import of
`ChatbotConfig` from `agent/config.py`. The type annotation on
`Neo4jManager.__init__`'s `config` parameter MUST be deferred using
`typing.TYPE_CHECKING` plus a quoted forward reference.

#### Scenario: Importing agent.config first does not raise ImportError
- **GIVEN** `agent/neo4j_manager.py` imports `ChatbotConfig` only under
  `if TYPE_CHECKING:` and annotates the parameter as `config: "ChatbotConfig" = None`
- **WHEN** `agent/config.py` is imported before anything else imports
  `agent.neo4j_manager`
- **THEN** no `ImportError: cannot import name 'ChatbotConfig' from
  partially initialized module 'agent.config'` MUST occur

#### Scenario: Importing agent.neo4j_manager first does not raise ImportError
- **GIVEN** the same `TYPE_CHECKING` guard is in place
- **WHEN** `agent/neo4j_manager.py` is imported before `agent/config.py`
- **THEN** the import MUST succeed, since `ChatbotConfig` is never
  referenced at runtime inside `neo4j_manager.py`

### Requirement: File Size Ceiling for Extracted Modules
Every newly created module in `src/agent/` (`state.py`,
`neo4j_manager.py`, `sql_processing.py`, `rag.py`, `config.py`) SHOULD stay
at or under ~500 lines. `graph.py`, as the orchestrator, is exempt from
this ceiling.

#### Scenario: Measuring line counts after extraction
- **GIVEN** all five modules have been extracted
- **WHEN** line counts are measured for each new file
- **THEN** none SHOULD exceed ~500 lines; any excess MUST be called out
  explicitly in the design or a follow-up note, not silently accepted
</content>
