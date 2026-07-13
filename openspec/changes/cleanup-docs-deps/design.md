# Design: Cleanup Dependencies and Dead Code

## Context

Two verified, low-risk cleanup items remain in scope after discarding two
false positives (`.env.example` mojibake, `langgraph.json`) confirmed fine by
manual audit. No architecture or behavior change is involved.

## Goals / Non-Goals

**Goals**
- Make `requirements.txt` reflect actual imports used by `vectorizador1.py`
- Remove dead commented-out code in `graph.py`

**Non-Goals**
- Restructuring `graph.py`
- Any change to `.env.example` or `langgraph.json`
- Adding tests (separate roadmap item)

## Decisions

### Decision: Pin versions to local venv, not latest

Add `pandas` and `psutil` to `requirements.txt` pinned to the exact versions
already installed and working in the project's `venv`, rather than latest
PyPI releases.

- **Rationale**: minimizes risk of introducing an untested version; the
  project already runs correctly against these installed versions.
- **Alternative considered**: pin to latest — rejected, adds unverified risk
  for zero benefit in a maintenance-only change.

### Decision: Delete commented block verbatim, no partial edits

Remove the entire `buscar_neo4j_completo` commented function and its
commented `asyncio.to_thread` call site (~lines 1363-1385) in one edit,
leaving surrounding explanatory comments untouched.

- **Rationale**: the block is fully superseded by the active sync Neo4j
  search path; partial removal risks leaving orphaned comment fragments.
- **Alternative considered**: keep as historical reference — rejected, dead
  code in a 1900+ line single file actively harms readability.

## Implementation Notes

1. `requirements.txt`: append `pandas==<installed_version>` and
   `psutil==<installed_version>` (query via `pip show pandas psutil` in the
   project venv before writing the pin).
2. `src/agent/graph.py`: delete the identified commented block only; no
   other lines in the file are touched.

## Risks / Trade-offs

| Risk | Mitigation |
|------|------------|
| Wrong version pin | Query `pip show` in local venv before writing |
| Accidentally removing active code near the dead block | Review diff line-by-line before commit; confirm `langgraph dev` still boots |

## Migration Plan

None — additive dependency entries and a pure deletion of inert code. No
data migration, no runtime behavior change.

## Open Questions

None.
