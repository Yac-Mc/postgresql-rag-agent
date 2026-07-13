# Design: Distinguish LLM Transient Errors from Security Rejections

## Technical Approach

`analizar_seguridad` (graph.py:331-510) currently funnels four distinct
outcomes into the same `except Exception` shape, making a Gemini 429/timeout
indistinguishable from a real security rejection. We add a `tipo` field to
`decision_seguridad` (`"rechazo_seguridad" | "error_transitorio"`), set it at
each of the four decision points, and propagate it unchanged through the
existing single-node routing (`rechazar_pregunta`, `manejar_respuesta_llm`,
`api.py`). No new graph nodes or edges — the existing conditional edge at
~line 112 already routes any `errores`-producing outcome to
`rechazar_pregunta`; we only change what happens *inside* that node and in
`api.py`, based on `tipo`.

## Architecture Decisions

| Decision | Choice | Alternative rejected | Rationale |
|---|---|---|---|
| Where to add `tipo` | Inside `decision_seguridad` dict (already `NotRequired[Dict]` in `State`) | New top-level `State` field `error_tipo` | `decision_seguridad` is already the single source of truth for the safety verdict; no `State`/`state.py` TypedDict schema change needed, since `Dict` accepts new keys at runtime without a `TypedDict` update |
| Exposing `tipo` to `api.py` | Copy `tipo` into `state["metadata"]["error_tipo"]` inside `rechazar_pregunta` (or directly in `analizar_seguridad` when short-circuiting on regex) | Add new `State` key `error_tipo` | `metadata` is already `NotRequired[Dict]`, already returned by `api.py` today (line 50) via `result.get("metadata", {})`, and already the app's convention for "extra info riding along the state" (used for `neo4j_id`/`timestamp` at line 980-983). Reusing it avoids touching `state.py` at all |
| Routing (new node vs. reuse) | Reuse `rechazar_pregunta` for both `tipo` values, branch on `tipo` inside the function | New `manejar_error_transitorio` node + new conditional edge | Proposal explicitly scopes out new retry/resiliency graph structure; the existing edge already captures "something is in `errores`, go here" — minimal-diff over restructuring the graph |
| Exception classes considered transitory | See list below (`google.api_core.exceptions` + stdlib network) | Only catch `Exception` broadly and rely on message-string sniffing | `ChatGoogleGenerativeAI.invoke()` (langchain-google-genai 2.0.10) surfaces errors from the underlying `google-api-core` 2.30.3 client, which raises structured, typed exceptions. Catching concrete types is more precise and future-proof than parsing `str(e)` |

### Confirmed exception classes (verified in installed `google.api_core.exceptions`, v2.30.3)

```python
from google.api_core.exceptions import (
    ResourceExhausted,     # 429 rate-limit / quota exhausted
    DeadlineExceeded,      # timeout
    ServiceUnavailable,    # 503
    InternalServerError,   # 500
    GatewayTimeout,        # 504
    Aborted,               # transient transactional conflict
    RetryError,            # exhausted an internal retry budget
    TooManyRequests,       # historical/compat alias of ResourceExhausted in
                           # some google-api-core versions; added for
                           # robustness against version-skew across
                           # environments (confirmed with user)
)
```

Plus stdlib network failures that can surface if the underlying `requests`/
`grpc` transport fails below the `google-api-core` wrapping layer:

```python
(ConnectionError, TimeoutError, OSError)
```

These are combined into a single tuple `TRANSIENT_LLM_EXCEPTIONS` defined
near the top of `graph.py` (or as a module constant in a small
`llm_errors.py` if preferred, but colocating in `graph.py` keeps the diff
minimal per the "no new modules unless needed" spirit of this change).

**Verification note**: classes were confirmed by introspecting the actually
installed `google.api_core.exceptions` module in this environment
(`google-api-core==2.30.3`, pulled transitively by `google-generativeai`,
which is a dependency of `langchain-google-genai==2.0.10`). No historical
Gemini traceback was found in Engram for this project to cross-check against
a real production error, so this list is based on the SDK's documented
exception hierarchy rather than an observed traceback. If a future incident
surfaces an exception type not in this tuple, the residual `except Exception`
fallback below still classifies it as `error_transitorio` (never
`rechazo_seguridad`), so no user is misled even if the tuple is incomplete —
only the specificity of the tuple, not the safety invariant, is at stake.

## Data Flow

    analizar_seguridad
      │
      ├─ regex match ──────────────────► decision_seguridad.tipo = "rechazo_seguridad"
      │                                   (unchanged: no LLM call)
      │
      ├─ LLM invoke raises
      │   TRANSIENT_LLM_EXCEPTIONS ────► decision_seguridad.tipo = "error_transitorio"
      │
      ├─ LLM invoke raises
      │   anything else (residual) ────► decision_seguridad.tipo = "error_transitorio"
      │                                   (conservative default, never rechazo_seguridad)
      │
      ├─ LLM responds, JSONDecodeError ► decision_seguridad.tipo = "error_transitorio"
      │
      └─ LLM responds, valid JSON
          es_segura: false/true ───────► decision_seguridad.tipo = "rechazo_seguridad"
                                          (only when es_segura is false; true → no error path)
              │
              ▼
      rechazar_pregunta (reads state["decision_seguridad"]["tipo"])
              │
              ├─ "rechazo_seguridad" → existing message (unchanged text)
              └─ "error_transitorio" → new "servicio no disponible" message
              │
              └─ writes state["metadata"]["error_tipo"] = tipo
              │
              ▼
      api.py POST /chat (reads result["metadata"].get("error_tipo"))
              │
              ├─ "error_transitorio" → HTTP 503
              └─ "rechazo_seguridad" / missing → HTTP 400 (current behavior)

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `src/agent/graph.py` | Modify | `analizar_seguridad` (~331-510): add `TRANSIENT_LLM_EXCEPTIONS` tuple constant; set `tipo` at all 4 branches (regex, `es_segura:false`, `JSONDecodeError`, invoke exception); split the current single `except Exception` around the invoke call into `except TRANSIENT_LLM_EXCEPTIONS` + residual `except Exception`, both resulting in `tipo: "error_transitorio"` |
| `src/agent/graph.py` | Modify | `rechazar_pregunta` (~993-1015): branch on `decision_seguridad.get("tipo")`; keep the existing message string verbatim for `"rechazo_seguridad"`; add new message for `"error_transitorio"`; write `state["metadata"]["error_tipo"] = tipo` |
| `src/agent/graph.py` | Modify | `stream_response` (~270-328): no direct code change required — it already surfaces `respuesta_natural` as-is; the new transient message flows through unchanged since it's produced by `rechazar_pregunta` |
| `src/agent/api.py` | Modify | POST `/chat` (~33-61): after `result.get("errores")` check, read `result.get("metadata", {}).get("error_tipo")`; if `"error_transitorio"` → `status_code=503`; else keep `status_code=400` |
| `src/agent/state.py` | No change | `decision_seguridad: NotRequired[Dict]` and `metadata: NotRequired[Dict]` already permit the new keys without a schema change |

## Interfaces / Contracts

```python
# decision_seguridad shape (informal, not a TypedDict today)
{
    "es_segura": bool,
    "razon": str,
    "riesgo": str,
    "tipo": Literal["rechazo_seguridad", "error_transitorio"],  # NEW
    # "palabras_peligrosas": list[str]  # only present on regex path, unchanged
}

# metadata addition (api.py contract)
{
    "error_tipo": Literal["rechazo_seguridad", "error_transitorio"],  # NEW, only present on error paths
    # existing keys (neo4j_id, timestamp) unaffected
}
```

## Testing Strategy

| Layer | What to Test | Approach |
|-------|-------------|----------|
| Unit | `analizar_seguridad` sets `tipo="rechazo_seguridad"` on regex match | Existing pattern, assert new field added |
| Unit | `analizar_seguridad` sets `tipo="rechazo_seguridad"` on `es_segura: false` | Mock `ChatGoogleGenerativeAI.invoke` to return valid JSON with `es_segura: false` |
| Unit | `analizar_seguridad` sets `tipo="error_transitorio"` for each of `ResourceExhausted`, `DeadlineExceeded`, `ServiceUnavailable`, `InternalServerError` | Mock `.invoke` to raise each exception (parametrized test) |
| Unit | `analizar_seguridad` sets `tipo="error_transitorio"` on unrecognized exception (e.g. plain `ValueError`) | Mock `.invoke` to raise `ValueError("boom")`, assert `tipo` still `error_transitorio`, never `rechazo_seguridad` |
| Unit | `analizar_seguridad` sets `tipo="error_transitorio"` on invalid JSON | Mock `.invoke` to return non-JSON string |
| Unit | `rechazar_pregunta` message content differs by `tipo`, security message text unchanged byte-for-byte | Assert exact string for `rechazo_seguridad`, assert no security wording for `error_transitorio` |
| Unit | `rechazar_pregunta` writes `metadata.error_tipo` | Assert dict key present and matches `decision_seguridad.tipo` |
| Unit (api) | `/chat` returns 503 when `metadata.error_tipo == "error_transitorio"` | `TestClient` with mocked `app.state.graph.ainvoke` return value |
| Unit (api) | `/chat` returns 400 for `rechazo_seguridad` and unrelated errors (regression) | Same harness, existing behavior preserved |

Suggested command:

```
pytest tests/ -k "analizar_seguridad or rechazar_pregunta or test_chat" -v
```

No integration tests against Postgres/Neo4j are required — this change is
isolated to in-process exception handling and dict/response shaping.

## Migration / Rollout

No migration required. No persisted state or schema changes. Revert = drop
the commit.

## Open Questions

- [x] Confirm `json.JSONDecodeError` should be `error_transitorio` — confirmed by user
- [x] Confirm the `TRANSIENT_LLM_EXCEPTIONS` tuple is sufficient, or whether to also add `google.api_core.exceptions.TooManyRequests` — confirmed by user, added to the tuple above
