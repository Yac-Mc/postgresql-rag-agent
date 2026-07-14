# Delta for Credential Management

## MODIFIED Requirements

### Requirement: No Hardcoded Real Secrets

The system MUST NOT contain real credentials (host, user, password, API keys, connection strings) hardcoded as literal values in any tracked source file. All access to external services (Postgres, Neo4j, Gemini) MUST resolve credentials via environment variables, without a real-value fallback. The `POST /chat` API key check MUST likewise resolve its expected key exclusively from the `API_KEY` environment variable, with no hardcoded or default value.
(Previously: only covered `DATABASE_URL`/Postgres, Neo4j, and Gemini credentials; did not mention `API_KEY`)

#### Scenario: Missing required env var raises explicit error

- GIVEN `DATABASE_URL` is not set in the environment
- WHEN `SQLRAGSystem.get_connection()` (graph.py) is called
- THEN the system raises an explicit `ValueError` naming the missing variable
- AND no hardcoded connection is attempted

#### Scenario: Standalone script fails fast without real credentials

- GIVEN `DATABASE_URL` is not set in the environment
- WHEN `vectorizador1.py` is executed
- THEN the script raises an explicit error before attempting any DB connection
- AND no hardcoded connection string is used

#### Scenario: Valid env var enables normal connection

- GIVEN `DATABASE_URL` is set to a valid Postgres connection string
- WHEN `SQLRAGSystem.get_connection()` or `vectorizador1.py` runs
- THEN the connection is established using only the env-provided value

#### Scenario: Missing API_KEY fails fast at startup

- GIVEN `API_KEY` is not set in the environment
- WHEN the FastAPI application (`api.py`) starts and the API key dependency
  is first resolved
- THEN the system raises an explicit error identifying `API_KEY` as missing
- AND no default or hardcoded key value is used to allow requests through

## ADDED Requirements

### Requirement: API_KEY Documented Alongside Other Required Secrets

`API_KEY` MUST be documented as a required environment variable in the same
place and format as `DATABASE_URL`, `GEMINI_API_KEY`, and `NEO4J_PASSWORD`
(e.g. `.env.example`), with no real value committed.

#### Scenario: .env.example lists API_KEY without a real value

- GIVEN a developer inspects `.env.example`
- WHEN they look for required credentials
- THEN `API_KEY` MUST be listed alongside `DATABASE_URL`, `GEMINI_API_KEY`,
  and `NEO4J_PASSWORD`
- AND its placeholder MUST NOT be a usable real key
