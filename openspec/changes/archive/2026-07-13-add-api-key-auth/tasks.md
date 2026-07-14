# Tasks: add-api-key-auth

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~80-110 |
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
| 1 | Add API key auth to `POST /chat`, env docs, tests, README roadmap checkboxes | PR 1 | Single cohesive security fix; small surface area, no split needed |

## Phase 1: Core Implementation

- [x] 1.1 In `src/agent/api.py`, add imports `Security` from `fastapi` and `APIKeyHeader` from `fastapi.security`
- [x] 1.2 At module level, read `API_KEY = os.getenv("API_KEY")`; raise `RuntimeError("API_KEY no esta definida en el entorno (.env)")` immediately if falsy (fail-fast at import time, before `app = FastAPI(...)` proceeds)
- [x] 1.3 Define `api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)` at module level
- [x] 1.4 Define `def verify_api_key(api_key: str = Security(api_key_header)) -> None`, raising `HTTPException(status_code=401, detail="Invalid or missing API key")` when `api_key != API_KEY`, with no interpolation of the received or expected value
- [x] 1.5 Add `dependencies=[Security(verify_api_key)]` to the `@app.post("/chat")` route decorator, leaving the `responder()` body unchanged

## Phase 2: Configuration

- [x] 2.1 Add `API_KEY=` line to `.env.example` alongside `DATABASE_URL`/`GEMINI_API_KEY`/`NEO4J_PASSWORD`, with no real value

## Phase 3: Testing

- [x] 3.1 In `tests/unit_tests/test_api.py`, following the existing `TestClient`/mock pattern, add `test_chat_returns_401_without_api_key_header`: `POST /chat` with no `X-API-Key` header, assert `response.status_code == 401`
- [x] 3.2 Add `test_chat_returns_401_with_incorrect_api_key`: `POST /chat` with `X-API-Key` set to a wrong value, assert `response.status_code == 401`, and assert the configured `API_KEY` value does not appear in `response.json()`
- [x] 3.3 Add `test_chat_returns_200_with_correct_api_key`: `POST /chat` with `X-API-Key` matching the test-configured `API_KEY`, mocking `app.state.graph.ainvoke` per existing pattern, assert `response.status_code == 200`

## Phase 4: Manual Verification

- [x] 4.1 Run `pytest` and confirm all tests pass, including the three new cases in `test_api.py`, with no regressions in existing suites
- [x] 4.2 Start the API locally with `API_KEY` set, open `/docs`, confirm the "Authorize" button is visible, enter the key, and confirm `POST /chat` succeeds via "Try it out"
- [x] 4.3 Fetch `GET /openapi.json` and confirm `components.securitySchemes` contains an `apiKey`-type scheme with `name: X-API-Key`, `in: header`, and that the `POST /chat` operation references it

## Phase 5: Documentation

- [x] 5.1 In `README.md`, under "Roadmap - Nuevas funcionalidades", convert `- \`add-conversation-memory\`` to `- [ ] \`add-conversation-memory\`` and add `- [ ] \`add-api-key-auth\`` (left unchecked here; flipped to `- [x]` during archive per `project-maintenance` spec)
