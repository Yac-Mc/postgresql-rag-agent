# Proposal: Refactor Graph Architecture

## Intent

`src/agent/graph.py` (1985 lines) mixes seven concerns in one file: config
(`ChatbotConfig`), Neo4j driver (`Neo4jGraph`, `Neo4jManager`), RAG/ChromaDB
(`SQLRAGSystem`), SQL parsing/validation/execution (`SQLProcessor`), schema
introspection (`obtener_ddl_dinamico`), and the LangGraph orchestrator
(`LangGraphAgent` with its 10 nodes: `analizar_seguridad`, `buscar_contexto`,
`generar_sql`, `validar_sql`, `ejecutar_sql`, `generar_respuesta`,
`manejar_respuesta_llm`, `indexar_consulta`, `rechazar_pregunta`,
`_build_graph`). This blocks unit testing in isolation, makes reviews harder,
and increases regression risk on every change. Split it into cohesive modules
without altering runtime behavior or public imports.

## Scope

### In Scope
- Extract `ChatbotConfig`, `SQLRAGSystem`, `Neo4jGraph`, `Neo4jManager`,
  `SQLProcessor`, `State`, `obtener_ddl_dinamico` into separate modules.
- Keep `LangGraphAgent` (nodes + `_build_graph`) and module-level
  `setup_graph()` / `agent = setup_graph()` in `graph.py` as orchestrator.
- Re-export every symbol currently importable from `agent.graph` so
  `api.py`, `__init__.py`, `langgraph.json`, and the 19 existing tests
  keep working unmodified (or with minimal, equivalent import updates).
- Preserve `agent = setup_graph()` executing at import time exactly as today
  (same side effects: Postgres/Neo4j/Gemini connections).

### Out of Scope
- Behavior changes to any node logic, prompts, SQL validation rules, or RAG
  scoring.
- Async/sync execution model changes.
- Splitting or renaming `db_bootstrap.py` (already separate).
- Performance optimization or dependency upgrades.

## Capabilities

### New Capabilities
- `graph-module-layout`: defines the target module split and the
  import-compatibility contract for `src/agent/`.

### Modified Capabilities
- None (no spec-level behavior changes; internal reorganization only).

## Approach

Incremental, test-gated extraction, one module at a time, running the full
suite after each step. **Corrected order** (see "Confirmed Circular
Dependency" below — extraction order is NOT simply "config first"):

1. `state.py` — `State` (TypedDict), zero dependencies.
2. `neo4j_manager.py` — `Neo4jGraph`, `Neo4jManager`. `Neo4jManager.__init__`
   type-hints `config: ChatbotConfig`, which must become a `TYPE_CHECKING`-
   guarded import (or string-quoted annotation) to avoid a real circular
   import with `config.py` — see below.
3. `sql_processing.py` — `SQLProcessor`, `obtener_ddl_dinamico`.
4. `rag.py` — `SQLRAGSystem` (no dependency on `ChatbotConfig` at
   construction time — verified: `SQLRAGSystem()` takes no args).
5. `config.py` — `ChatbotConfig`, which imports `SQLProcessor` (real,
   instantiated directly in `__init__`) and `Neo4jManager` (real,
   instantiated in `_init_neo4j`) — both already extracted by this point.
6. `graph.py` stays as orchestrator: `LangGraphAgent`, `setup_graph()`,
   `agent = setup_graph()`, plus re-imports of all extracted symbols so
   `from agent.graph import X` keeps resolving.

No new abstractions, interfaces, or DI container — pure move + re-export.

### Confirmed Circular Dependency: ChatbotConfig ↔ Neo4jManager

Verified in the current code (not assumed):
- `ChatbotConfig.__init__` (graph.py:132) does `self.sql_processor = SQLProcessor()` — real, eager import needed.
- `ChatbotConfig._init_neo4j` (graph.py:436) does `self.neo4j_manager = Neo4jManager(self.neo4j_graph, self)` — real, eager import needed.
- `Neo4jManager.__init__` (graph.py:632) signature is `def __init__(self, graph: Neo4jGraph, config: ChatbotConfig = None):` — a real (non-string, non-`TYPE_CHECKING`) type annotation. `graph.py` has no `from __future__ import annotations`, so this annotation is evaluated eagerly at class-definition time (module import time).

If `config.py` imports `Neo4jManager` from `neo4j_manager.py`, and `neo4j_manager.py` imports `ChatbotConfig` from `config.py` for the type hint, this is a genuine circular import that raises `ImportError: cannot import name 'ChatbotConfig' from partially initialized module 'agent.config'` in Python.

**Required fix during design**: guard the `ChatbotConfig` import in `neo4j_manager.py` with `typing.TYPE_CHECKING` and quote the annotation (`config: "ChatbotConfig" = None`), which defers evaluation without needing `from __future__ import annotations` project-wide. This must be specified explicitly in `sdd-design`, not left as a generic "avoid circular imports" risk.

## Affected Areas

| Area | Impact | Description |
|------|--------|--------------|
| `src/agent/graph.py` | Modified | Reduced to orchestrator + re-exports |
| `src/agent/config.py` | New | `ChatbotConfig` |
| `src/agent/sql_processing.py` | New | `SQLProcessor`, `obtener_ddl_dinamico` |
| `src/agent/rag.py` | New | `SQLRAGSystem` |
| `src/agent/neo4j_manager.py` | New | `Neo4jGraph`, `Neo4jManager` |
| `src/agent/state.py` | New | `State` TypedDict |
| `tests/**` | Verify only | 19 tests must pass unmodified or with equivalent imports |
| `api.py`, `__init__.py`, `langgraph.json` | Verify only | Must resolve unchanged |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Circular import between `config.py` and `neo4j_manager.py` | **Confirmed, not just theoretical** — see "Confirmed Circular Dependency" section above | `TYPE_CHECKING` guard + quoted annotation on `Neo4jManager.__init__`'s `config` param; extraction order: `neo4j_manager.py` before `config.py` |
| `setup_graph()` side effects change on import | Medium | Diff import behavior before/after each step; run full suite |
| Breaking `langgraph.json` graph attribute | Low | Keep `graph` global set inside `graph.py` unchanged |
| Hidden coupling via shared globals | Medium | Grep all cross-class references before each extraction |
| Test imports silently pass but runtime API breaks | Low | Add smoke run of `api.py` startup after refactor |

## Rollback Plan

Each extraction step is its own commit. If any step fails tests or breaks
imports, `git revert` that commit only — prior steps remain intact. If the
full refactor proves unsafe mid-way, revert to the last commit before this
change; `graph.py` is fully self-contained today, so reverting cannot
cascade into other Phase 2 items.

## Dependencies

- Requires `add-test-coverage` (already archived) — the 19 tests are the
  safety net for this refactor.

## Success Criteria

- [ ] All 19 existing tests pass unmodified (or with only import-path
      updates preserving intent).
- [ ] `api.py`, `__init__.py`, and `langgraph.json` work without changes.
- [ ] `agent = setup_graph()` still executes at import time with identical
      side effects (manual end-to-end smoke test).
- [ ] No file in `src/agent/` exceeds ~500 lines except `graph.py`
      orchestrator (target reduction, not hard gate).
