# Api Authentication Specification

## Purpose

Protect `POST /chat` from unauthenticated access via a single global API key
sent in the `X-API-Key` header, using a FastAPI security scheme
(`APIKeyHeader` + `Security()`) so the mechanism is both enforced at runtime
and declared in the generated OpenAPI schema.

## Requirements

### Requirement: API Key Required on POST /chat

The `POST /chat` endpoint MUST require a valid `X-API-Key` header, validated
against the `API_KEY` environment variable, before executing any agent logic.

#### Scenario: Missing X-API-Key header is rejected

- GIVEN `POST /chat` is called without an `X-API-Key` header
- WHEN the request reaches the endpoint
- THEN the system MUST respond with HTTP 401 Unauthorized
- AND the agent graph MUST NOT be invoked

#### Scenario: Incorrect X-API-Key value is rejected

- GIVEN `POST /chat` is called with `X-API-Key` set to a value that does not
  match `API_KEY`
- WHEN the request reaches the endpoint
- THEN the system MUST respond with HTTP 401 Unauthorized
- AND the agent graph MUST NOT be invoked

#### Scenario: Correct X-API-Key value is accepted

- GIVEN `POST /chat` is called with `X-API-Key` matching `API_KEY`
- WHEN the request reaches the endpoint
- THEN the system MUST process the request with unchanged current behavior

### Requirement: 401 Error Message Does Not Leak Expected Key

Any 401 response produced by the API key check MUST use a fixed, generic
message and MUST NOT include the expected `API_KEY` value or hints about its
format or length.

#### Scenario: Generic message on missing header

- GIVEN `POST /chat` is called without `X-API-Key`
- WHEN the 401 response body is built
- THEN the message MUST be a fixed generic string (e.g. "Invalid or missing
  API key")
- AND the message MUST NOT contain the value of `API_KEY`

#### Scenario: Generic message on incorrect key

- GIVEN `POST /chat` is called with an incorrect `X-API-Key`
- WHEN the 401 response body is built
- THEN the message MUST be the same fixed generic string used for the
  missing-header case
- AND the message MUST NOT echo back the received or expected key value

### Requirement: OpenAPI Security Scheme via APIKeyHeader

The API key check MUST be implemented using `fastapi.security.APIKeyHeader`
declared with `Security(...)` as a route dependency, not a manually parsed
`Header()` parameter, so FastAPI registers a `securitySchemes` entry in the
generated OpenAPI schema.

#### Scenario: OpenAPI schema declares the security scheme

- GIVEN the application is running with the API key dependency applied to
  `POST /chat`
- WHEN the OpenAPI schema is fetched (e.g. `GET /openapi.json`)
- THEN `components.securitySchemes` MUST contain an `apiKey`-type scheme with
  `name: X-API-Key` and `in: header`
- AND the `POST /chat` operation MUST reference that security scheme

#### Scenario: Swagger UI shows and honors the Authorize button

- GIVEN a developer opens `/docs` (Swagger UI)
- WHEN the page renders
- THEN an "Authorize" button MUST be visible
- AND entering a valid API key there MUST allow `POST /chat` to be invoked
  successfully from the Swagger UI "Try it out" panel

### Requirement: Reusable Security Dependency Pattern

The API key dependency MUST be defined once and be attachable to any future
route via the same `Security(...)` parameter, without requiring per-route
duplication of the validation logic.

#### Scenario: Dependency reused on a second endpoint

- GIVEN a new endpoint is added and declares the same API key dependency via
  `Security(...)`
- WHEN that endpoint is called without a valid `X-API-Key`
- THEN it MUST return HTTP 401 using the same validation logic and message
  as `POST /chat`, with no duplicated key-comparison code
