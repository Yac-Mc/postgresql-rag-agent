# Llm Error Classification Specification

## Purpose

Classify the outcome of `analizar_seguridad` into a real security rejection
versus a transient LLM-provider error, and propagate that distinction
honestly to HTTP status codes and user-facing messages, without ever
allowing an unsafe question to reach SQL generation/execution.

## Requirements

### Requirement: Security Decision Type Classification

The system MUST set a `tipo` field on `decision_seguridad` with one of two
values: `"rechazo_seguridad"` or `"error_transitorio"`.

#### Scenario: Dangerous keyword detected by regex

- GIVEN a question containing a keyword matched by the pre-LLM regex check
- WHEN `analizar_seguridad` runs
- THEN `decision_seguridad.tipo` MUST equal `"rechazo_seguridad"`
- AND the LLM MUST NOT be invoked

#### Scenario: LLM explicitly evaluates the question as unsafe

- GIVEN the LLM call succeeds and returns valid JSON with `es_segura: false`
- WHEN `analizar_seguridad` processes the response
- THEN `decision_seguridad.tipo` MUST equal `"rechazo_seguridad"`

#### Scenario: LLM invocation raises a rate-limit, timeout, or network exception

- GIVEN the call to Gemini raises an exception representing rate-limit,
  timeout, or network/service failure (mockable in tests)
- WHEN `analizar_seguridad` catches the exception
- THEN `decision_seguridad.tipo` MUST equal `"error_transitorio"`
- AND the question MUST NOT be treated as evaluated for safety

#### Scenario: LLM responds with invalid JSON

- GIVEN the LLM call succeeds but the response body fails JSON parsing
  (`json.JSONDecodeError`)
- WHEN `analizar_seguridad` processes the response
- THEN `decision_seguridad.tipo` MUST equal `"error_transitorio"`
- AND this MUST NOT be classified as `"rechazo_seguridad"`

#### Scenario: Unrecognized exception during LLM invocation

- GIVEN an exception is raised during the LLM call that does not match any
  known rate-limit/timeout/network/JSON-decode class
- WHEN `analizar_seguridad` catches it via the residual `except Exception`
- THEN `decision_seguridad.tipo` MUST default to `"error_transitorio"`
- AND MUST NOT default to `"rechazo_seguridad"`

### Requirement: Distinct User-Facing Message by Classification Type

`rechazar_pregunta` MUST generate a message that matches the classification
`tipo` of `decision_seguridad`.

#### Scenario: Message for a real security rejection

- GIVEN `decision_seguridad.tipo == "rechazo_seguridad"`
- WHEN `rechazar_pregunta` builds `respuesta_natural`
- THEN the message MUST state that the request cannot be processed for
  security reasons and MUST suggest formulating a SELECT-only query

#### Scenario: Message for a transient error

- GIVEN `decision_seguridad.tipo == "error_transitorio"`
- WHEN `rechazar_pregunta` (or the equivalent transient-error path) builds
  `respuesta_natural`
- THEN the message MUST state that the service is temporarily unavailable
  and invite the user to retry shortly
- AND the message MUST NOT suggest the question was unsafe

### Requirement: HTTP Status Mapping in /chat Endpoint

The `POST /chat` endpoint in `api.py` MUST map the error `tipo` to a
distinct HTTP status code.

#### Scenario: Transient LLM error maps to 503

- GIVEN `result.get("errores")` is non-empty AND the underlying error is
  classified as `"error_transitorio"`
- WHEN the `/chat` endpoint builds its response
- THEN it MUST return HTTP 503 Service Unavailable
- AND the response body MUST NOT imply a security rejection

#### Scenario: Security rejection or other existing error maps to 400

- GIVEN `result.get("errores")` is non-empty AND the underlying error is
  classified as `"rechazo_seguridad"`, OR is an unrelated existing error
  (e.g. SQL execution failure)
- WHEN the `/chat` endpoint builds its response
- THEN it MUST return HTTP 400, preserving current behavior

### Requirement: Safety Invariant Preserved Under Both Classifications

Neither classification MUST allow SQL generation or execution to proceed
for a question that was not confirmed safe.

#### Scenario: Real rejection blocks SQL path

- GIVEN `decision_seguridad.tipo == "rechazo_seguridad"`
- WHEN the graph continues execution
- THEN it MUST NOT reach SQL generation or execution nodes

#### Scenario: Transient error blocks SQL path

- GIVEN `decision_seguridad.tipo == "error_transitorio"`
- WHEN the graph continues execution
- THEN it MUST NOT reach SQL generation or execution nodes, since safety
  was never actually confirmed
