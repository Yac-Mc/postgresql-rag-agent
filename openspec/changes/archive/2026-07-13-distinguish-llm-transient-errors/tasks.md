# Tasks: Distinguish LLM Transient Errors from Security Rejections

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~90-130 (2 prod files ~40-50 lines + 2 test files ~50-80 lines new) |
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
| 1 | Full change: graph.py + api.py + tests | PR 1 | Single cohesive PR; well under 400-line budget, no natural split boundary (api.py change is tiny and depends on graph.py's new field) |

## Phase 1: Foundation

- [x] 1.1 In `src/agent/graph.py`, add module-level `TRANSIENT_LLM_EXCEPTIONS` tuple importing `ResourceExhausted, DeadlineExceeded, ServiceUnavailable, InternalServerError, GatewayTimeout, Aborted, RetryError, TooManyRequests` from `google.api_core.exceptions`, plus `(ConnectionError, TimeoutError, OSError)`, per design.md.

## Phase 2: Core Implementation (`analizar_seguridad`)

- [x] 2.1 Regex branch: set `decision_seguridad["tipo"] = "rechazo_seguridad"` alongside existing `es_segura/razon/riesgo/palabras_peligrosas` dict.
- [x] 2.2 `es_segura: false` explicit branch (valid JSON, LLM says unsafe): set `decision_seguridad["tipo"] = "rechazo_seguridad"`.
- [x] 2.3 Split the current single `except Exception` around `_invocar_sync()`/`.invoke()` into `except TRANSIENT_LLM_EXCEPTIONS` (specific) + residual `except Exception` (fallback) — both set `decision_seguridad["tipo"] = "error_transitorio"`, never `rechazo_seguridad`.
- [x] 2.4 `except json.JSONDecodeError` branch: set `decision_seguridad["tipo"] = "error_transitorio"` (not `rechazo_seguridad`).
- [x] 2.5 Verify the outer residual `except Exception` (wrapping the whole method body) also defaults `tipo` to `"error_transitorio"`, per the safety invariant in spec.md.

## Phase 3: Integration (`rechazar_pregunta` + `api.py`)

- [x] 3.1 In `rechazar_pregunta`, read `decision_seguridad.get("tipo")`; keep the existing `respuesta_natural` message verbatim for `"rechazo_seguridad"`.
- [x] 3.2 Add a new `respuesta_natural` message for `"error_transitorio"`: states the service is temporarily unavailable, invites retry, and does NOT mention security/safety.
- [x] 3.3 In `rechazar_pregunta`, write `state["metadata"]["error_tipo"] = tipo` (init `metadata` dict if absent) before returning.
- [x] 3.4 In `src/agent/api.py` `/chat` endpoint, after the `result.get("errores")` check, read `result.get("metadata", {}).get("error_tipo")`; if `"error_transitorio"` raise `HTTPException(status_code=503, ...)` with the transient message; otherwise keep `status_code=400` (unchanged).

## Phase 4: Testing

- [x] 4.1 In `tests/unit_tests/test_security_analysis.py`, add test: LLM invoke raises each of `ResourceExhausted`, `DeadlineExceeded`, `TooManyRequests` (parametrized), mock `agent.graph.ChatGoogleGenerativeAI`, assert `decision_seguridad["tipo"] == "error_transitorio"` and `es_segura` is not asserted true (safety never confirmed).
- [x] 4.2 In `tests/unit_tests/test_security_analysis.py`, add test: LLM invoke raises unrecognized exception (e.g. `ValueError("boom")`), assert `decision_seguridad["tipo"] == "error_transitorio"`, never `"rechazo_seguridad"`.
- [x] 4.3 In `tests/unit_tests/test_security_analysis.py`, add test: LLM `.invoke` returns non-JSON string content, assert `decision_seguridad["tipo"] == "error_transitorio"`.
- [x] 4.4 In `tests/unit_tests/test_security_analysis.py`, add/extend existing dangerous-keyword and `es_segura: false` tests to assert `decision_seguridad["tipo"] == "rechazo_seguridad"` (regression coverage for the new field on already-passing paths).
- [x] 4.5 Add test for `rechazar_pregunta`: given `decision_seguridad = {"tipo": "rechazo_seguridad", ...}`, assert `respuesta_natural` matches the existing security-rejection text verbatim and `metadata["error_tipo"] == "rechazo_seguridad"`.
- [x] 4.6 Add test for `rechazar_pregunta`: given `decision_seguridad = {"tipo": "error_transitorio", ...}`, assert `respuesta_natural` contains the new transient-service message and does NOT contain security-rejection wording, and `metadata["error_tipo"] == "error_transitorio"`.
- [x] 4.7 Create `tests/unit_tests/test_api.py` using FastAPI `TestClient` (httpx is installed): mock `app.state.graph.ainvoke` to return a result with `metadata.error_tipo == "error_transitorio"`, assert `POST /chat` returns 503.
- [x] 4.8 In `tests/unit_tests/test_api.py`, add regression test: mock `ainvoke` result with `errores` present and `metadata.error_tipo == "rechazo_seguridad"` (or missing `error_tipo`), assert `POST /chat` still returns 400.

## Phase 5: Verification

- [x] 5.1 Run `pytest -m "not integration" -q` and confirm all tests pass, including the new ones from Phase 4. **Result: 31 passed, 2 deselected.**

## Phase 6: Critical fix found during sdd-verify (routing safety invariant)

Independent verification found that `after_security_analysis` (the graph's conditional-edge
routing function) string-sniffed `"seguridad"` inside free-text error messages instead of
reading the new structured `decision_seguridad.tipo` field. The dangerous-keyword regex
rejection message ("Consulta contiene operaciones peligrosas: ...") never contains the word
"seguridad", so a flagged question was NOT routed to `rechazar_pregunta` and could fall
through to `buscar_contexto` -> `generar_sql`, violating the spec's own
"Safety Invariant Preserved Under Both Classifications" requirement. This routing function
pre-dates this change but was never wired to the new `tipo` field.

- [x] 6.1 Extract routing logic to a module-level `route_after_security_analysis(state)` function
      in `src/agent/graph.py` (before `class LangGraphAgent`), keying off
      `state.get("decision_seguridad", {}).get("tipo")` instead of string-matching "seguridad".
- [x] 6.2 Update the `after_security_analysis` closure inside `_build_graph` to delegate:
      `return route_after_security_analysis(state)`.
- [x] 6.3 Add 3 regression tests in `tests/unit_tests/test_security_analysis.py` calling
      `route_after_security_analysis` directly: dangerous-keyword rejection (the exact bug
      scenario), transient-error rejection, and the safe path (`buscar_contexto`).
- [x] 6.4 Re-run `pytest -m "not integration" -q`. **Result: 31 passed, 2 deselected, 0 failures.**
- [x] 6.5 Independent re-verification (sdd-verify) confirmed PASS with no remaining issues.
