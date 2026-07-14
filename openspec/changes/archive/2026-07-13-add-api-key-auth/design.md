# Design: add-api-key-auth

## Technical Approach

Add a single reusable FastAPI security dependency in `src/agent/api.py` using
`fastapi.security.APIKeyHeader` + `Security(...)`, following the existing
env-var pattern (`os.getenv`, no real-value fallback) already used for
`GEMINI_API_KEY` in `graph.py`. The dependency is declared once at module
level and attached to `POST /chat` via the route decorator's `dependencies=`
list, so it is trivially reusable on future endpoints without duplicating
comparison logic. No new project dependency, no config module introduced
(the project has none today — env vars are read via direct `os.getenv` calls
at the point of use, e.g. `graph.py:75`).

## Architecture Decisions

### Decision: Fail fast at import time if `API_KEY` is unset

| Option | Tradeoff | Decision |
|---|---|---|
| Fail at import time (module load) | App refuses to start at all if `API_KEY` missing; matches how a missing security control should behave — no silent unprotected endpoint | **Chosen** |
| Fail lazily on first request | App starts "successfully" but every request 401s or (worse) if implemented carelessly could accept anything | Rejected |
| Fallback to empty string default | Endpoint becomes effectively unprotected (`X-API-Key: ` empty compares true against empty default) | Rejected |

**Rationale**: proposal and spec (`credential-management`) explicitly require the same fail-fast pattern as `DATABASE_URL`. Unlike `GEMINI_API_KEY` (read lazily inside `graph.py` functions), `API_KEY` protects an endpoint — starting a server that silently has no real protection is worse than refusing to start. Read `os.getenv("API_KEY")` once at module level in `api.py`, raise `RuntimeError` immediately if `None`/empty, before `app = FastAPI(...)` proceeds further.

### Decision: `Security()` + `APIKeyHeader`, not manual `Header()`

| Option | Tradeoff | Decision |
|---|---|---|
| Manual `x_api_key: str = Header(None)` | Simple, but FastAPI does NOT register it in `components.securitySchemes` → no Authorize button in `/docs` | Rejected |
| `APIKeyHeader(name="X-API-Key", auto_error=False)` + `Security(verify_api_key)` | Registers `apiKey` scheme in OpenAPI schema, enables Swagger "Authorize" button (explicit proposal/spec requirement) | **Chosen** |

**Rationale**: spec requirement "OpenAPI Security Scheme via APIKeyHeader" mandates this exact mechanism for `/docs` Authorize button support. `auto_error=False` is required so `verify_api_key` controls the 401 message instead of FastAPI's generic 403 "Not authenticated".

### Decision: Dependency at route level (`dependencies=[...]` on `@app.post`), not global middleware

| Option | Tradeoff | Decision |
|---|---|---|
| Global `app.middleware("http")` | Protects all routes uniformly, but harder to selectively exempt future public endpoints (e.g. `/health`) and less explicit in code/OpenAPI per-route | Rejected |
| Per-route `dependencies=[Security(verify_api_key)]` on `@app.post("/chat", ...)` | Explicit, self-documenting per endpoint; matches proposal step 4 ("no middleware global"); trivially copy-pasted to new routes | **Chosen** |

**Rationale**: proposal explicitly calls for per-route application to keep it "explicito por endpoint y facil de replicar en endpoints futuros" (Reusable Security Dependency Pattern requirement). Since `verify_api_key` is a standalone function, reuse means adding the same `dependencies=[Security(verify_api_key)]` entry to any new route — no duplicated comparison logic.

## Data Flow

    Client request
      │  X-API-Key: <value>
      ▼
    APIKeyHeader(auto_error=False)  ──extracts header, or None if absent
      │
      ▼
    verify_api_key(api_key)
      │  compare api_key == API_KEY (module-level constant, read at import)
      │
      ├─ mismatch/missing ──▶ raise HTTPException(401, generic message)
      │
      └─ match ──▶ dependency resolves, request proceeds to responder()
                        │
                        ▼
                   existing /chat logic (graph.ainvoke) — unchanged

## File Changes

| File | Action | Description |
|------|--------|--------------|
| `src/agent/api.py` | Modify | Import `Security`, `APIKeyHeader`; read `API_KEY = os.getenv("API_KEY")` at module level with fail-fast `RuntimeError` if unset; define `api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)` and `def verify_api_key(api_key: str = Security(api_key_header)) -> None`; add `dependencies=[Security(verify_api_key)]` to `@app.post("/chat")` |
| `.env.example` | Modify | Add `API_KEY=` line alongside `DATABASE_URL`/`GEMINI_API_KEY`/`NEO4J_PASSWORD`, no real value |
| `README.md` | Modify | Convert "Roadmap - Nuevas funcionalidades" bullets to markdown checkboxes; `add-conversation-memory` stays `- [ ]`; `add-api-key-auth` added as `- [ ]` now, flipped to `- [x]` at archive time (per `project-maintenance` spec) |

## Interfaces / Contracts

```python
# src/agent/api.py
import os
from fastapi import FastAPI, HTTPException, Security
from fastapi.security import APIKeyHeader

API_KEY = os.getenv("API_KEY")
if not API_KEY:
    raise RuntimeError("API_KEY no esta definida en el entorno (.env)")

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def verify_api_key(api_key: str = Security(api_key_header)) -> None:
    if api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


@app.post("/chat", dependencies=[Security(verify_api_key)])
async def responder(pregunta: Pregunta):
    ...  # unchanged
```

README diff expected:

```diff
 ## Roadmap - Nuevas funcionalidades

 Funcionalidad nueva, no cubierta por la Fase 2 (que fue refactor/limpieza de lo
 existente):
-- `add-conversation-memory`
+- [ ] `add-conversation-memory`
+- [x] `add-api-key-auth`
```

(`add-api-key-auth` is written as `- [ ]` during apply, then flipped to
`- [x]` at archive time per the `project-maintenance` spec scenario.)

## Testing Strategy

| Layer | What to Test | Approach |
|-------|--------------|----------|
| Unit | `verify_api_key` rejects missing header | Call function directly with `api_key=None`, assert `HTTPException(401)` |
| Unit | `verify_api_key` rejects wrong key | Call with mismatched string, assert 401, assert message doesn't contain the real key |
| Unit | `verify_api_key` accepts correct key | Call with matching string, assert no exception raised |
| Integration | `POST /chat` without header → 401 | `TestClient` request without `X-API-Key`, assert status 401 |
| Integration | `POST /chat` with wrong header → 401 | Same, with incorrect `X-API-Key` value |
| Integration | `POST /chat` with correct header → 200 (unchanged behavior) | Same, with `X-API-Key` matching test-configured `API_KEY` |
| Manual | `/docs` shows Authorize button and works end-to-end | Open Swagger UI, click Authorize, enter key, invoke `/chat` via "Try it out" |
| Manual | `GET /openapi.json` declares `apiKey` scheme | Fetch schema, confirm `components.securitySchemes` entry with `name: X-API-Key`, `in: header` |

## Migration / Rollout

No data migration. Every environment running this app (including local dev)
must set `API_KEY` in `.env` before the app will start — this is a breaking
change to local setup, documented in `.env.example` and README. No phased
rollout needed; single global key, no backward-compat mode planned (matches
proposal's explicit fail-fast risk mitigation).

## Open Questions

None — proposal and specs fully define required behavior, error message
contract, and OpenAPI scheme requirements.
