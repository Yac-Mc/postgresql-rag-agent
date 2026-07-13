# Proposal: Cleanup Dependencies, Docs, and Dead Code

## Intent

Phase 1 cleanup (requirements.txt, README, pyproject.toml unification) was done at
project inception but not re-audited against the actual code. This proposal fixes
concrete, verified issues found by auditing imports, docs, and file structure —
no speculative refactors, this is a thesis/personal project.

## Scope

### In Scope
- Add `pandas` and `psutil` to `requirements.txt` (imported in `vectorizador1.py`,
  currently missing — script would fail on a clean install). Verified via
  direct import grep against `vectorizador1.py` and absence in `requirements.txt`.
- Remove dead commented-out code block in `graph.py` (`buscar_neo4j_completo`
  function + its `asyncio.to_thread` call site, ~15 lines, superseded by the
  current sync Neo4j search path)

### Out of Scope
- `.env.example` encoding — **verified NOT corrupted**. An earlier audit pass
  claimed mojibake (`v�a` instead of `vía`); this was wrong. Checked at the
  byte level: the file contains valid UTF-8 (`í` = `0xC3 0xAD`, confirmed via
  `open(..., encoding='utf-8')` reading the correct codepoint U+00ED). The
  `�` only appears when this specific Windows console (cp1252 codepage)
  tries to *print* the character — a terminal display limitation, not a file
  problem. No action needed.
- `langgraph.json` — **verified working, not touching it**. An earlier audit
  pass incorrectly flagged `graph.py:graph` as missing; this was wrong. The
  module sets a real module-level `graph` attribute via `global graph` inside
  `_build_graph()` (graph.py:910), executed at import time by
  `agent = setup_graph()` (graph.py:1939). Confirmed working today: both
  `langgraph dev` (Studio UI) and a direct `import agent.graph as g; g.graph`
  check succeeded end-to-end in this same session.
- Restructuring `graph.py` (1900+ lines, single-file agent) — that's a design-level
  change, not cleanup
- Adding test coverage — separate roadmap item
- Removing other `# comment` lines that are genuine explanatory notes (only
  commented-out CODE is removed, not documentation comments)

## Capabilities

### New Capabilities
None.

### Modified Capabilities
None — this is a maintenance change with no spec-level behavior change.

## Approach

1. Add missing deps to `requirements.txt` (pin to installed versions in `venv`)
2. Delete the dead commented block in `graph.py`

## Affected Areas

| Area | Impact | Description |
|------|--------|--------------|
| `requirements.txt` | Modified | Add `pandas`, `psutil` |
| `src/agent/graph.py` | Modified | Remove ~15 lines of dead commented code |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Pinning pandas/psutil to wrong version | Low | Pin to versions already installed in local `venv` |

## Rollback Plan

All changes are file edits with no runtime dependency shifts beyond adding two
already-in-use packages. Revert via `git checkout -- <file>` per affected file.

## Dependencies

None.

## Success Criteria

- [ ] `pip install -r requirements.txt` + running `vectorizador1.py` no longer
      relies on ambient/pre-installed `pandas`/`psutil`
- [ ] No commented-out dead code remains in `graph.py`
- [ ] `.env.example` and `langgraph.json` untouched (both confirmed fine, not part of this change)
- [ ] `langgraph dev` still works end-to-end (regression check)
