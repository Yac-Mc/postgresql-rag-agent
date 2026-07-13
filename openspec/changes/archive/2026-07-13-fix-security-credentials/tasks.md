# Tasks: fix-security-credentials

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~90-120 |
| 400-line budget risk | Low |
| Chained PRs recommended | No |
| Suggested split | Single PR |
| Delivery strategy | ask-on-risk |

### Suggested Work Units

| Unit | Goal | Likely PR | Notes |
|------|------|-----------|-------|
| 1 | Remove hardcoded credentials, add scanner, update docs | PR 1 | Single cohesive security fix; no split needed |

## Phase 1: Credential Removal

- [x] 1.1 In `src/agent/graph.py`, replace `SQLRAGSystem.get_connection()` hardcoded connection params with `database_url = os.getenv("DATABASE_URL")`; raise `ValueError("DATABASE_URL no está definida en el entorno (.env)")` if unset; call `psycopg2.connect(database_url, sslmode="require")`
- [x] 1.2 In `src/agent/vectorizador1.py`, replace hardcoded `DB_URL` string with `os.getenv("DATABASE_URL")` + `ValueError` raise if unset, keep `create_engine(DB_URL)` call unchanged

## Phase 2: Secret-Scanning Tool

- [x] 2.1 Create `scripts/check_secrets.py`: standalone script (no CI wiring) that walks `git ls-files` tracked files, regex-matches `user:pass@host` connection-string patterns and known API key prefixes (`gsk_`, `AIza`, `glpat-`), prints file:line findings, exits 0 if none found
- [x] 2.2 Manually verify script flags a planted fake credential in a temp file and passes clean on current repo state after 1.1/1.2

## Phase 3: Documentation

- [x] 3.1 Add "Seguridad" section to `README.md`: note that a real database password was exposed in commit `6da906b` and must be rotated manually with the relevant provider
- [x] 3.2 In the same README section, document how to run `python scripts/check_secrets.py` before committing

## Phase 4: Verification

- [x] 4.1 Run `python -c "import ast; ast.parse(open('src/agent/graph.py').read())"` to confirm `graph.py` is syntactically valid
- [x] 4.2 Run `python -c "import ast; ast.parse(open('src/agent/vectorizador1.py').read())"` to confirm `vectorizador1.py` is syntactically valid
- [x] 4.3 With `DATABASE_URL` set, confirm the modified connection code works end-to-end: verified both via the full graph (LangGraph Studio + API, reaching the same Gemini-call point as pre-change, no new earlier failures) and via a direct call to `SQLRAGSystem().get_connection()` (bypassing Gemini/quota) that connects and executes a query successfully. Found and fixed a real regression along the way: hardcoded `sslmode="require"` broke local Postgres connections (no SSL configured); removed it entirely (see design.md Open Questions).
- [x] 4.4 Run `python scripts/check_secrets.py` against the current repo state and confirm no findings — passed clean.
