# Design: fix-security-credentials

## Technical Approach
Remove the 2 remaining hardcoded external-provider secrets by reusing the existing `DATABASE_URL` env-var pattern already established in `ChatbotConfig._init_postgresql()` (graph.py) and `db_bootstrap.py`, instead of introducing a new/parallel var. Add a minimal secret-scanning script and a README note on manual credential rotation.

## Architecture Decisions

### Decision: Reuse DATABASE_URL instead of new EXTERNAL_DB_URL
| Option | Tradeoff | Decision |
|---|---|---|
| New `EXTERNAL_DB_URL` var | Isolates RAG-search DB from app DB; but adds a 2nd var to keep in sync, extra `.env.example` entry, and diverges from existing convention | Rejected |
| Reuse `DATABASE_URL` | One connection string across `ChatbotConfig`, `SQLRAGSystem`, and `vectorizador1.py`; matches `db_bootstrap.py` comment ("evitar mismatches... bug real encontrado en una sesión anterior") | **Chosen** |

**Rationale**: `SQLRAGSystem.get_connection()` and `vectorizador1.py` connect to the same Postgres instance as `ChatbotConfig`. Splitting into a second variable reintroduces the exact class of bug `db_bootstrap.py` already documents as previously fixed (DB identity split across two env vars going out of sync). `DATABASE_URL` is already required and validated (`_init_postgresql` raises `ValueError` if missing) — no new `.env.example` entry needed.

### Decision: No fallback default value on missing env var
**Choice**: `os.getenv("DATABASE_URL")` + explicit `raise ValueError(...)` if `None`, mirroring `_init_postgresql`.
**Alternatives considered**: Silent `os.getenv("DATABASE_URL", "")` fallback → rejected, causes confusing downstream connection errors instead of a clear failure.
**Rationale**: Consistency with existing code; fail-fast is safer for credential handling.

### Decision: Secret-scanning as a standalone script, not full pre-commit framework
**Choice**: `scripts/check_secrets.py` — small regex scanner run manually.
**Alternatives considered**: Full `gitleaks` CI integration or `.pre-commit-config.yaml` with multiple hooks → rejected as over-engineering for a personal/thesis project.
**Rationale**: Proportional to project scale; a documented manual/optional check satisfies the requirement without CI maintenance burden.

## File Changes

| File | Action | Description |
|---|---|---|
| `src/agent/graph.py` | Modify | `SQLRAGSystem.get_connection()`: replace hardcoded params with `psycopg2.connect(os.getenv("DATABASE_URL"))`, raise `ValueError` if unset |
| `src/agent/vectorizador1.py` | Modify | Replace `DB_URL = "postgresql://...Paloma2695147-..."` with `DB_URL = os.getenv("DATABASE_URL")`, raise `ValueError` if unset |
| `scripts/check_secrets.py` | Create | Regex scanner for `user:pass@host` patterns, known key prefixes |
| `README.md` | Modify | Add "Seguridad" section: rotate the exposed password (exposed in commit `6da906b`), note on running `check_secrets.py` before commits |
| `.env.example` | No change | `DATABASE_URL` already documented; no new var introduced |

## Interfaces / Contracts

```python
# src/agent/graph.py — SQLRAGSystem.get_connection()
def get_connection(self):
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL no está definida en el entorno (.env)")
    return psycopg2.connect(database_url)
```

```python
# src/agent/vectorizador1.py
DB_URL = os.getenv("DATABASE_URL")
if not DB_URL:
    raise ValueError("DATABASE_URL no está definida en el entorno (.env)")
engine = create_engine(DB_URL)
```

## Testing Strategy

| Layer | What to Test | Approach |
|---|---|---|
| Unit | `get_connection()` raises `ValueError` when `DATABASE_URL` unset | Mock `os.getenv` → `None`, assert exception |
| Manual | `check_secrets.py` flags a planted hardcoded string | Run script against a temp file with a fake `user:pass@host` literal |
| Manual | Runtime connection still works with real `DATABASE_URL` | Run existing app against local Postgres with env var set |

## Migration / Rollout
No data migration. Deploy requires `DATABASE_URL` to be set in every environment that previously relied on the hardcoded external-provider values (already true for `ChatbotConfig`, so no new deployment step). User must manually rotate the exposed password with the relevant provider — outside code scope.

## Open Questions
- [x] Confirm whether `sslmode="require"` should stay hardcoded — **Resuelto durante verificación (Fase 4)**: se encontró un bug real, `sslmode="require"` rompía la conexión contra el Postgres local de Docker (`psycopg2.OperationalError: server does not support SSL, but SSL was required`). Se removió por completo del código; si una instancia específica necesita SSL, se agrega como query param en el propio `DATABASE_URL` (`?sslmode=require`), no hardcodeado. Verificado con conexión real post-fix: `SQLRAGSystem().get_connection()` conecta y ejecuta consultas correctamente contra Postgres local.
