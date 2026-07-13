# Tasks: Cleanup Dependencies and Dead Code

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~25 (2 added, ~21 deleted) |
| 400-line budget risk | Low |
| Chained PRs recommended | No |
| Suggested split | Single PR |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Low

### Suggested Work Units

| Unit | Goal | Likely PR | Notes |
|------|------|-----------|-------|
| 1 | Pin pandas/psutil + delete dead Neo4j block | PR 1 | Single self-contained maintenance PR; base = main |

## Phase 1: Investigation

- [x] 1.1 Run `pip show pandas psutil` in the project venv, capture exact `Version:` values for both packages.
- [x] 1.2 Confirm `src/agent/graph.py` lines 1362-1382 are exactly the commented `buscar_neo4j_completo` block + its `asyncio.to_thread` call (already verified: lines 1362-1382, surrounded by active code at 1360 and 1383).

## Phase 2: Implementation

- [x] 2.1 In `requirements.txt`, add `pandas==<version>` alphabetically between `packaging==24.2` and `pillow==11.3.0`.
- [x] 2.2 In `requirements.txt`, add `psutil==<version>` alphabetically between `protobuf>=3.20.2,<6.0.0` and `psycopg2-binary==2.9.10`.
- [x] 2.3 In `src/agent/graph.py`, delete lines 1362-1382 (the commented `buscar_neo4j_completo` function, its commented `asyncio.to_thread` call, and the blank/comment lines directly within that block only) - leave lines 1360-1361 and 1383-1386 untouched.

## Phase 3: Verification

- [x] 3.1 Run `python -c "import ast; ast.parse(open('src/agent/graph.py', encoding='utf-8').read())"` - must exit without error (valid syntax).
- [x] 3.2 Verify the two pinned versions exist on PyPI (`pip index versions pandas`, `pip index versions psutil`, or `pip install --dry-run -r requirements.txt` in a scratch venv) - no resolution errors.
- [x] 3.3 Run `langgraph dev` and confirm it reaches the same startup point as before this change (no new errors, no regressions) - same pass/fail bar as the prior cleanup change.

## Phase 4: Cleanup

- [x] 4.1 Review the diff for `graph.py` line-by-line to confirm no active code was removed alongside the dead block.
