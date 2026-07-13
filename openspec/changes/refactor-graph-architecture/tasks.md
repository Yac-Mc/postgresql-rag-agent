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
- [ ] 1.4 Commit: `refactor(agent): extract State to state.py`

## Commit 2: `neo4j_manager.py`

- [x] 2.1 Move `Neo4jGraph` (L105–121) + `Neo4jManager` (L631–759) verbatim to `src/agent/neo4j_manager.py`; import `neo4j.GraphDatabase`
- [x] 2.2 Apply `TYPE_CHECKING` guard: `if TYPE_CHECKING: from .config import ChatbotConfig`; quote annotation `config: "ChatbotConfig" = None`
- [x] 2.3 In `graph.py`: remove both classes, add `from .neo4j_manager import Neo4jGraph, Neo4jManager`
- [x] 2.4 Verify: `pytest -m "not integration"` green; `python -c "import agent.neo4j_manager"` succeeds standalone; explicit check `import agent.neo4j_manager` does NOT trigger `agent.config` import (no circular import)
- [ ] 2.5 Commit: `refactor(agent): extract Neo4jGraph and Neo4jManager to neo4j_manager.py`

## Commit 3: `sql_processing.py`

- [ ] 3.1 Move `obtener_ddl_dinamico` (L39–85) + `SQLProcessor` (L760–877) verbatim to `src/agent/sql_processing.py`; import `sqlalchemy.inspect`, `re`, `json`
- [ ] 3.2 In `graph.py`: remove both, add `from .sql_processing import SQLProcessor, obtener_ddl_dinamico`; keep `from sqlalchemy import inspect` local for patch compatibility
- [ ] 3.3 Verify: `pytest -m "not integration"` green; `python -c "import agent.sql_processing"` smoke import; `test_sql_processor.py` patch target `agent.graph.inspect` still resolves
- [ ] 3.4 Commit: `refactor(agent): extract SQLProcessor and obtener_ddl_dinamico to sql_processing.py`

## Commit 4: `rag.py`

- [ ] 4.1 Move `SQLRAGSystem` (L562–630) verbatim to `src/agent/rag.py`; import `chromadb`, `numpy`, `psycopg2`, `sentence_transformers.SentenceTransformer`, `sklearn.metrics.pairwise.cosine_similarity`, `urllib.parse.urlparse`
- [ ] 4.2 In `graph.py`: remove class, add `from .rag import SQLRAGSystem`; keep `import psycopg2` local for patch compatibility
- [ ] 4.3 Verify: `pytest -m "not integration"` green; `python -c "import agent.rag"` smoke import; `test_configuration.py` patch target `agent.graph.psycopg2.connect` still resolves
- [ ] 4.4 Commit: `refactor(agent): extract SQLRAGSystem to rag.py`

## Commit 5: `config.py`

- [ ] 5.1 Move `ChatbotConfig` (L122–561) verbatim to `src/agent/config.py`; import `.sql_processing.SQLProcessor`, `.neo4j_manager.Neo4jManager`/`Neo4jGraph`, `sqlalchemy.create_engine`/`sessionmaker`, `os`, `pathlib.Path`
- [ ] 5.2 In `graph.py`: remove class, add `from .config import ChatbotConfig`
- [ ] 5.3 Verify: `pytest -m "not integration"` green; `python -c "import agent.config"` smoke import; confirm no circular import by importing `agent.config` in isolation before `agent.graph`
- [ ] 5.4 Commit: `refactor(agent): extract ChatbotConfig to config.py`

## Commit 6: `graph.py` orchestrator

- [ ] 6.1 Add re-export block (`State`, `Neo4jGraph`, `Neo4jManager`, `SQLProcessor`, `obtener_ddl_dinamico`, `SQLRAGSystem`, `ChatbotConfig`) plus kept direct imports `psycopg2`, `inspect`, `ChatGoogleGenerativeAI`
- [ ] 6.2 Confirm `LangGraphAgent` (L878–1899), `setup_graph()`, `agent = setup_graph()` untouched
- [ ] 6.3 Verify: `pytest -m "not integration"` green; all 19 tests import via `agent.graph` unmodified
- [ ] 6.4 Commit: `refactor(agent): reduce graph.py to orchestrator with re-exports`

## Final Verification

- [ ] 7.1 `pytest -m integration` against real Docker (`postgres-local`, `neo4j-local`)
- [ ] 7.2 Smoke test `api.py` startup — no import errors, same log output
- [ ] 7.3 Smoke test `langgraph dev` (or `python -c "from agent.graph import graph"` equivalent) resolves `agent.graph:graph`
- [ ] 7.4 Measure line counts of `state.py`, `neo4j_manager.py`, `sql_processing.py`, `rag.py`, `config.py` against ~500-line ceiling; flag any excess explicitly (expected: `config.py` ~440 lines, all others well under)
</content>
