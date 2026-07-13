# Tasks: Refactor Graph Architecture

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~1950 total across 6 commits (per-commit below) |
| 400-line budget risk | High (commit 5 alone) |
| Chained PRs recommended | Yes |
| Suggested split | 6 chained PRs, one per commit, stacked in order |
| Delivery strategy | ask-on-risk |
| Chain strategy | stacked-to-main |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Est. lines | Notes |
|------|------|-----------|-----------|-------|
| 1 | Extract `state.py` | PR 1 | ~40 | Base = main; zero deps |
| 2 | Extract `neo4j_manager.py` + `TYPE_CHECKING` fix | PR 2 | ~300 | Base = PR1; resolves confirmed circular dep |
| 3 | Extract `sql_processing.py` | PR 3 | ~330 | Base = PR2 |
| 4 | Extract `rag.py` | PR 4 | ~140 | Base = PR3 |
| 5 | Extract `config.py` | PR 5 | ~880 | Base = PR4; **exceeds budget**, largest single move (440-line class) — no safe further split, ask user for `size:exception` on this slice only |
| 6 | Reduce `graph.py` to orchestrator + re-exports | PR 6 | ~40 | Base = PR5; final integration + verification |

## Commit 1: `state.py`

- [x] 1.1 Move `State` (TypedDict, current L86–104) verbatim to `src/agent/state.py` with `typing`/`typing_extensions` imports
- [x] 1.2 In `graph.py`: remove `State` definition, add `from .state import State`
- [x] 1.3 Verify: `pytest -m "not integration"` green; `python -c "import agent.state"` smoke import isolated
- [x] 1.4 Commit: `refactor(agent): extract State to state.py` (`1d75d1a`)

## Commit 2: `neo4j_manager.py`

- [x] 2.1 Move `Neo4jGraph` (L105–121) + `Neo4jManager` (L631–759) verbatim to `src/agent/neo4j_manager.py`; import `neo4j.GraphDatabase`
- [x] 2.2 Apply `TYPE_CHECKING` guard: `if TYPE_CHECKING: from .config import ChatbotConfig`; quote annotation `config: "ChatbotConfig" = None`
- [x] 2.3 In `graph.py`: remove both classes, add `from .neo4j_manager import Neo4jGraph, Neo4jManager`
- [x] 2.4 Verify: `pytest -m "not integration"` green; `python -c "import agent.neo4j_manager"` succeeds standalone; explicit check `import agent.neo4j_manager` does NOT trigger `agent.config` import (no circular import)
- [x] 2.5 Commit: `refactor(agent): extract Neo4jGraph and Neo4jManager to neo4j_manager.py` (`3fefb59`)

## Commit 3: `sql_processing.py`

- [x] 3.1 Move `obtener_ddl_dinamico` (L39–85) + `SQLProcessor` (L760–877) verbatim to `src/agent/sql_processing.py`; import `sqlalchemy.inspect`, `re`, `json`
- [x] 3.2 In `graph.py`: remove both, add `from .sql_processing import SQLProcessor, obtener_ddl_dinamico`; keep `from sqlalchemy import inspect` local for patch compatibility
- [x] 3.3 Verify: `pytest -m "not integration"` green; `python -c "import agent.sql_processing"` smoke import; patch target corrected to `agent.sql_processing.inspect` (functions resolve globals in the module where defined, not where re-exported)
- [x] 3.4 Commit: `refactor(agent): extract SQLProcessor and obtener_ddl_dinamico to sql_processing.py` (`a155f30`)

## Commit 4: `rag.py`

- [x] 4.1 Move `SQLRAGSystem` verbatim to `src/agent/rag.py`; actual imports needed (confirmed by reading code): `os`, `psycopg2`, `sentence_transformers.SentenceTransformer` (chromadb/numpy/sklearn/urlparse are NOT used inside this class body)
- [x] 4.2 In `graph.py`: remove class, add `from .rag import SQLRAGSystem`; kept `import psycopg2` local for other code in graph.py still using it
- [x] 4.3 Verify: `pytest -m "not integration"` green (17/17); `python` isolated module-load smoke import of `agent.rag` succeeded; patch target in `test_configuration.py` corrected to `agent.rag.psycopg2.connect` (same root cause as commit 3 — function globals resolve in defining module)
- [x] 4.4 Commit: `refactor(agent): extract SQLRAGSystem to rag.py` (`4e1fa98`)

## Commit 5: `config.py`

- [x] 5.1 Move `ChatbotConfig` (L122–561) verbatim to `src/agent/config.py`; import `.sql_processing.SQLProcessor`, `.neo4j_manager.Neo4jManager`/`Neo4jGraph`, `sqlalchemy.create_engine`/`sessionmaker`, `os`, `pathlib.Path`
- [x] 5.2 In `graph.py`: remove class, add `from .config import ChatbotConfig`
- [x] 5.3 Verify: `pytest -m "not integration"` green; `python -c "import agent.config"` smoke import; confirm no circular import by importing `agent.config` in isolation before `agent.graph`
- [x] 5.4 Commit: `refactor(agent): extract ChatbotConfig to config.py` (`ae5939d`) — accepted as a size exception (single verbatim move of a ~440-line class), per explicit user decision

## Commit 6: `graph.py` orchestrator

- [x] 6.1 Add re-export block (`State`, `Neo4jGraph`, `Neo4jManager`, `SQLProcessor`, `obtener_ddl_dinamico`, `SQLRAGSystem`, `ChatbotConfig`) plus kept direct import `ChatGoogleGenerativeAI` (note: `psycopg2`/`inspect` direct imports turned out unnecessary — no remaining test patches `agent.graph.psycopg2`/`agent.graph.inspect`, both were already corrected to `agent.rag.psycopg2`/`agent.sql_processing.inspect` in commits 3-4)
- [x] 6.2 Confirm `LangGraphAgent`, `setup_graph()`, `agent = setup_graph()` untouched
- [x] 6.3 Verify: `pytest -m "not integration"` green; all 19 tests import via `agent.graph` unmodified
- [x] 6.4 Commit: `refactor(agent): reduce graph.py to orchestrator with re-exports` (`54fca6f`)

## Commit 7 (unplanned, found during manual verification): langgraph dev import compatibility

- [x] 7.1 Found real regression: `langgraph dev` loads `graph.py` by file path (`importlib.util.spec_from_file_location`), not as part of the `agent` package — relative imports (`from .state import State`, etc.) fail there with `ImportError: attempted relative import with no known parent package`. Neither pytest nor a direct `import agent.graph` catches this, since both use proper package context.
- [x] 7.2 Fix: try/except pattern in `graph.py` and `config.py` — attempt the relative import first (works under pytest/api.py/direct import), fall back to a `sys.path`-adjusted absolute import (works under `langgraph dev`'s file-path load) if it raises `ImportError`.
- [x] 7.3 Verify: `langgraph dev --no-reload --tunnel` loads the graph without `ImportError`; Studio UI connects and successfully runs a real invocation end-to-end.
- [x] 7.4 Commit: `fix(agent): compatibilidad de imports con langgraph dev (carga por ruta de archivo)` (`2ce079d`)

## Final Verification

- [x] 8.1 `pytest -m integration` against real Docker (`postgres-local`, `neo4j-local`) — 2 passed
- [x] 8.2 Smoke test `api.py` startup — no import errors, same log output
- [x] 8.3 Smoke test `langgraph dev --tunnel` resolves `agent.graph:graph` and Studio UI connects (required the commit 7 fix above; not anticipated in the original design)
- [x] 8.4 Line counts measured: `state.py` 24, `neo4j_manager.py` 156, `sql_processing.py` 169, `rag.py` 73, `config.py` 471, `graph.py` 1157 (only `graph.py` exceeds ~500 lines, accepted as the orchestrator exception per the spec)
</content>
