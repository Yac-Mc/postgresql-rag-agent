# Design: Refactor Graph Architecture

## Technical Approach

Pure move-and-re-export refactor of `src/agent/graph.py` (1985 actual lines,
verified directly and matching the proposal; internal line references for
`ChatbotConfig.__init__` L132, `_init_neo4j` L436, and
`Neo4jManager.__init__` L632 are confirmed accurate). Six sequential
extractions, each its own commit, each gated by the full test suite. No
new abstractions — classes/functions move verbatim; `graph.py` becomes a
thin orchestrator that re-imports every symbol so `agent.graph` keeps its
current public surface, including third-party names (`psycopg2`,
`inspect`, `ChatGoogleGenerativeAI`) that tests patch directly.

## Architecture Decisions

| Decision | Choice | Alternative rejected | Rationale |
|---|---|---|---|
| Extraction order | `state.py` → `neo4j_manager.py` → `sql_processing.py` → `rag.py` → `config.py` → `graph.py` | `config.py` first (natural reading order) | `config.py` first would move `ChatbotConfig` before `Neo4jManager`, so `neo4j_manager.py` would need a real import of `ChatbotConfig` for its constructor's default flow — the fix below avoids this entirely, but ordering still minimizes churn per step |
| Circular import fix | `TYPE_CHECKING` guard + quoted annotation in `neo4j_manager.py` | `from __future__ import annotations` project-wide | Global future-import changes evaluation semantics for every annotation in every file touched later; the guard is a one-line, localized fix with zero blast radius |
| Re-export style | `graph.py` does direct `from .module import Name` for every symbol, no `__all__` needed unless `from agent.graph import *` is used elsewhere (verified: it is not) | Lazy re-export via `__getattr__` (PEP 562) | Direct imports are simpler, statically analyzable by IDEs/linters, and match the existing flat style of the codebase |
| Third-party patch targets | `graph.py` keeps its own top-level `import psycopg2` and `from sqlalchemy import inspect`, even though the code using them moved | Update test patch paths to `agent.rag.psycopg2` / `agent.sql_processing.inspect` | Proposal's constraint is "tests pass unmodified or with import-path updates preserving intent" — keeping the import in `graph.py` requires ZERO test changes, which is the stronger compliance option |
| Module boundaries | One file per concern per proposal's Affected Areas table | Merging `rag.py` + `sql_processing.py` (both touch SQL) | Proposal explicitly lists them as separate files; `SQLRAGSystem` (ChromaDB/embeddings) and `SQLProcessor` (validation/execution) have no shared state, so merging would reintroduce mixed concerns |

## Data Flow (import graph after split)

    state.py  (State TypedDict — zero deps)
        ^
        |
    neo4j_manager.py  (Neo4jGraph, Neo4jManager)
        |  TYPE_CHECKING-only import of ChatbotConfig
        v
    sql_processing.py  (SQLProcessor, obtener_ddl_dinamico)
        ^
        |
    rag.py  (SQLRAGSystem — no ChatbotConfig dependency at construction)
        ^
        |
    config.py  (ChatbotConfig — real imports: SQLProcessor, Neo4jManager)
        ^
        |
    graph.py  (LangGraphAgent, setup_graph(), agent = setup_graph())
              + re-exports: State, Neo4jGraph, Neo4jManager, SQLProcessor,
                obtener_ddl_dinamico, SQLRAGSystem, ChatbotConfig
              + kept locally: psycopg2, inspect, ChatGoogleGenerativeAI
                (mock-patch compatibility, see Decision table)

No module below `graph.py` imports anything above it in this chain, so
there is no cycle once the `TYPE_CHECKING` guard is applied.

## File Changes

| File | Action | Description |
|------|--------|--------------|
| `src/agent/state.py` | Create | `State(TypedDict)` moved verbatim (current L86–104). Imports: `typing.Annotated/Dict/List/Optional/Union`, `typing_extensions.TypedDict/NotRequired`. |
| `src/agent/neo4j_manager.py` | Create | `Neo4jGraph` (L105–121) + `Neo4jManager` (L631–759) moved verbatim. Imports: `neo4j.GraphDatabase`; `typing.TYPE_CHECKING`; under `if TYPE_CHECKING: from .config import ChatbotConfig`. `__init__` signature becomes `def __init__(self, graph: Neo4jGraph, config: "ChatbotConfig" = None):`. |
| `src/agent/sql_processing.py` | Create | `obtener_ddl_dinamico` (L39–85) + `SQLProcessor` (L760–877) moved verbatim. Imports: `sqlalchemy.inspect`, `re`, `json`. |
| `src/agent/rag.py` | Create | `SQLRAGSystem` (L562–630) moved verbatim. Imports: `chromadb`, `numpy`, `psycopg2`, `sentence_transformers.SentenceTransformer`, `sklearn.metrics.pairwise.cosine_similarity`, `urllib.parse.urlparse`. |
| `src/agent/config.py` | Create | `ChatbotConfig` (L122–561) moved verbatim. Imports: `.sql_processing.SQLProcessor`, `.neo4j_manager.Neo4jManager`, `.neo4j_manager.Neo4jGraph`, `sqlalchemy.create_engine`, `sqlalchemy.orm.sessionmaker`, `os`, `pathlib.Path`. |
| `src/agent/graph.py` | Modify | Reduced to: stdlib/third-party imports still needed by `LangGraphAgent` + the mock-patch-compatibility imports (`psycopg2`, `inspect`, `ChatGoogleGenerativeAI`); re-import block for all extracted symbols; `LangGraphAgent` (L878–1899, unchanged); `setup_graph()` + `agent = setup_graph()` (unchanged). |
| `tests/**` | Verify only | No changes expected (see Test Impact Verification below) |
| `api.py`, `__init__.py`, `langgraph.json` | Verify only | No changes expected |

## `graph.py` Re-export Block (exact)

```python
# --- Re-exports for backward-compatible `from agent.graph import X` ---
from .state import State
from .neo4j_manager import Neo4jGraph, Neo4jManager
from .sql_processing import SQLProcessor, obtener_ddl_dinamico
from .rag import SQLRAGSystem
from .config import ChatbotConfig

# Kept as direct imports (not just re-exports) so existing tests can still
# patch these names via `patch("agent.graph.<name>...")`. mock.patch
# mutates the shared module object, so it does not matter that
# rag.py / sql_processing.py also import these independently.
import psycopg2
from sqlalchemy import inspect
from langchain_google_genai import ChatGoogleGenerativeAI
```

No `__all__` is added: the codebase has no existing `from agent.graph
import *` usage (verified across `tests/`, `api.py`, `__init__.py`), so an
explicit `__all__` would add a maintenance burden without a consumer that
needs it.

## Interfaces / Contracts

No new interfaces. `Neo4jManager.__init__` signature change (annotation
only, not behavior):

```python
# neo4j_manager.py
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import ChatbotConfig  # import-time no-op, type-checker only

class Neo4jManager:
    def __init__(self, graph: Neo4jGraph, config: "ChatbotConfig" = None):
        ...
```

`config.py`'s `ChatbotConfig._init_neo4j` (current L436) keeps its real,
eager call unchanged:

```python
# config.py
from .neo4j_manager import Neo4jManager, Neo4jGraph

class ChatbotConfig:
    def __init__(self):
        ...
        self.sql_processor = SQLProcessor()          # real import from .sql_processing
        ...
    def _init_neo4j(self):
        ...
        self.neo4j_manager = Neo4jManager(self.neo4j_graph, self)  # real, runtime-only
```

This is safe because `neo4j_manager.py` never evaluates `ChatbotConfig` at
runtime — the annotation is a string, and the `TYPE_CHECKING` block is
`False` at runtime, so `config.py` can freely import `Neo4jManager` without
`neo4j_manager.py` needing to import `config.py` back.

## Test Impact Verification (confirmed by reading actual test files)

| Test file | Exact import from `agent.graph` | Patch targets on `agent.graph` | Impact after split |
|---|---|---|---|
| `tests/unit_tests/test_configuration.py` | `from agent.graph import ChatbotConfig, SQLRAGSystem` | `patch("agent.graph.psycopg2.connect", ...)` | None — both re-exported; `psycopg2` kept as direct import in `graph.py` |
| `tests/unit_tests/test_sql_processor.py` | `from agent.graph import SQLProcessor, obtener_ddl_dinamico` | `patch("agent.graph.inspect", ...)` | None — both re-exported; `inspect` kept as direct import in `graph.py` |
| `tests/unit_tests/test_security_analysis.py` | `from agent.graph import LangGraphAgent` | `patch("agent.graph.ChatGoogleGenerativeAI")` | None — `LangGraphAgent` never leaves `graph.py`; `ChatGoogleGenerativeAI` already imported there for node use |
| `tests/unit_tests/test_db_bootstrap.py` | none (`from agent.db_bootstrap import _parse_connection`) | none | None — untouched module |
| `tests/integration_tests/test_graph.py` | `from agent.graph import ChatbotConfig, SQLProcessor` | none | None — both re-exported |

**Conclusion**: zero test files require any modification. All 19 tests
pass unmodified given the re-export block above.

## Testing Strategy

| Layer | What to Test | Approach |
|-------|-------------|----------|
| Unit | Each extracted module importable standalone (`import agent.state`, `import agent.neo4j_manager`, etc.) | Add a throwaway `python -c "import agent.X"` smoke check per commit step (not a permanent test, just a gate) |
| Unit | Existing 19 tests, unmodified | `pytest -m "not integration"` after every extraction commit |
| Integration | Real Postgres/Neo4j smoke | `pytest -m integration` (opt-in, requires Docker) after the final commit |
| Manual | `api.py` startup + `agent = setup_graph()` side effects | Run `python api.py` (or equivalent entrypoint) once at the end, confirm no import errors and same log output as pre-refactor |

## Migration / Rollout

No data migration. Rollout is the commit sequence itself (see Plan of
Commits below). No feature flag needed — this is an internal reorg with
identical runtime behavior.

## Plan of Commits

Each commit: extract one module → update `graph.py` imports/re-exports →
run `pytest -m "not integration"` → run manual smoke of `api.py` import →
commit only if green.

1. **`refactor(agent): extract State to state.py`** — zero deps, safest first step. Verify: full suite green.
2. **`refactor(agent): extract Neo4jGraph and Neo4jManager to neo4j_manager.py`** — apply `TYPE_CHECKING` guard immediately (this commit is the one that resolves the confirmed circular dependency). Verify: full suite green, explicit check that `import agent.neo4j_manager` alone succeeds without importing `agent.config`.
3. **`refactor(agent): extract SQLProcessor and obtener_ddl_dinamico to sql_processing.py`** — verify: full suite green, `test_sql_processor.py` patch targets still resolve.
4. **`refactor(agent): extract SQLRAGSystem to rag.py`** — verify: full suite green, `test_configuration.py` patch targets still resolve.
5. **`refactor(agent): extract ChatbotConfig to config.py`** — real imports of `SQLProcessor` and `Neo4jManager` now resolve cleanly since both already exist. Verify: full suite green, confirm no circular import by importing `agent.config` in isolation before `agent.graph`.
6. **`refactor(agent): reduce graph.py to orchestrator with re-exports`** — add the re-export block, confirm `LangGraphAgent`/`setup_graph`/`agent = setup_graph()` untouched. Verify: full suite green + `pytest -m integration` (if Docker available) + manual `api.py` smoke run.

After step 6, re-measure line counts for all five new files against the
~500-line ceiling and flag any file that exceeds it as a follow-up note
(expected: `config.py` at ~440 lines and `LangGraphAgent`'s host,
`graph.py`, at ~1000+ lines as the accepted orchestrator exception).

## Open Questions

- [x] Line count verified: graph.py is 1985 lines, matching the proposal
      exactly. Internal line references (L132, L436, L632) also confirmed
      accurate.
- [ ] None blocking.
