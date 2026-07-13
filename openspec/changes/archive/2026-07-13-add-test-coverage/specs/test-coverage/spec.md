# Test Coverage Specification

## Purpose

Define the automated test suite covering pure logic in `src/agent/graph.py`
and `src/agent/db_bootstrap.py`, split into a fast offline unit suite (no
Gemini, no Docker) and an opt-in integration suite against real local
Postgres/Neo4j.

## Requirements

### Requirement: Offline Unit Suite Runs Fast and Green

The system MUST provide a unit test suite that runs via
`pytest -m "not integration"` without any network calls, without invoking
`ChatGoogleGenerativeAI`, and without requiring Docker containers.

#### Scenario: Running the offline suite

- GIVEN no Docker containers running and no `GOOGLE_API_KEY` configured
- WHEN a developer runs `pytest -m "not integration"`
- THEN all unit tests pass
- AND no test raises a network or authentication error

#### Scenario: Gemini client never invoked in unit tests

- GIVEN `ChatGoogleGenerativeAI` is mocked at the import boundary
- WHEN unit tests covering `analizar_seguridad` execute
- THEN the mock's call count is asserted to be exactly 0 for dangerous-keyword
  cases
- AND asserted to be exactly 1 (mocked, not real) for safe-query cases that
  reach the LLM branch

### Requirement: SQLProcessor.validate_sql Coverage

The system MUST test `SQLProcessor.validate_sql` (`graph.py:765`) for both
valid and invalid SQL inputs, without requiring a live database connection.

#### Scenario: Valid SELECT statement

- GIVEN a syntactically valid `SELECT` statement
- WHEN `validate_sql` is called
- THEN it returns a dict with `is_valid: True`

#### Scenario: Invalid or non-SELECT statement

- GIVEN a malformed SQL string or a non-`SELECT` statement (e.g. `DROP TABLE`)
- WHEN `validate_sql` is called
- THEN it returns a dict with `is_valid: False`
- AND includes a non-empty `error` message

### Requirement: SQLProcessor.parse_sql_to_ast Coverage

The system MUST test `SQLProcessor.parse_sql_to_ast` (`graph.py:793`) to
confirm it extracts table and column references from valid SQL.

#### Scenario: Parsing a simple SELECT

- GIVEN a valid `SELECT column FROM table` statement
- WHEN `parse_sql_to_ast` is called
- THEN the returned AST dict includes the referenced table name(s)
- AND includes the referenced column name(s)

#### Scenario: Parsing malformed SQL

- GIVEN a malformed SQL string
- WHEN `parse_sql_to_ast` is called
- THEN the method either raises a handled exception or returns an
  empty/error-marked AST, matching its current documented behavior

### Requirement: obtener_ddl_dinamico Coverage with Mocked Inspector

The system MUST test `obtener_ddl_dinamico` using a mocked SQLAlchemy
`Inspector`, without connecting to a real database.

#### Scenario: DDL generation from mocked schema

- GIVEN a mocked SQLAlchemy `inspect()` result exposing table names, columns,
  and primary keys
- WHEN `obtener_ddl_dinamico` is called
- THEN the returned DDL string includes each mocked table name
- AND includes each mocked column definition

#### Scenario: Empty schema

- GIVEN a mocked inspector reporting zero tables
- WHEN `obtener_ddl_dinamico` is called
- THEN the returned DDL string does not raise an exception
- AND reflects the absence of tables (empty or explicitly-marked content)

### Requirement: db_bootstrap._parse_connection Coverage

The system MUST test `_parse_connection` (`db_bootstrap.py:26`), a pure
function with no I/O, for standard and edge-case connection strings.

#### Scenario: Standard connection string

- GIVEN `postgresql://user:pass@localhost:5432/mydb`
- WHEN `_parse_connection` is called
- THEN it returns `conn_params` with `user`, `password`, `host`, `port` matching
  the input
- AND returns `db_name` equal to `"mydb"`

#### Scenario: Missing port defaults to 5432

- GIVEN a connection string with no explicit port
- WHEN `_parse_connection` is called
- THEN `conn_params["port"]` equals `5432`

#### Scenario: Case-sensitive database name preserved

- GIVEN a connection string with a mixed-case database name (e.g. `/Usuarios`)
- WHEN `_parse_connection` is called
- THEN `db_name` preserves the original casing exactly

### Requirement: Dangerous Keyword Detection in analizar_seguridad

The system MUST test the dangerous-keyword short-circuit in
`analizar_seguridad` (`graph.py:1159`) with `ChatGoogleGenerativeAI` mocked
and never actually invoked when a dangerous keyword is found.

#### Scenario: Question contains a dangerous keyword

- GIVEN a question containing a dangerous keyword (e.g. `"eliminar"`)
- WHEN `analizar_seguridad` is called
- THEN `state["decision_seguridad"]["es_segura"]` is `False`
- AND `state["errores"]` contains a message referencing the dangerous
  operation
- AND the mocked `ChatGoogleGenerativeAI` is never invoked

#### Scenario: Question contains no dangerous keyword

- GIVEN a question with no dangerous keyword and a mocked LLM response of
  `{"es_segura": true, "razon": "...", "riesgo": "bajo"}`
- WHEN `analizar_seguridad` is called
- THEN `state["decision_seguridad"]["es_segura"]` is `True`
- AND the mocked LLM is invoked exactly once

### Requirement: DATABASE_URL Missing Regression Guard

The system MUST have a regression test proving that `ChatbotConfig`
raises `ValueError` when `DATABASE_URL` is unset or empty.

#### Scenario: DATABASE_URL unset

- GIVEN the `DATABASE_URL` environment variable is unset
- WHEN `ChatbotConfig._init_postgresql()` runs (not `__init__`, which never
  reads this variable)
- THEN a `ValueError` is raised

### Requirement: sslmode Never Hardcoded Regression Guard

The system MUST have a regression test proving `sslmode` is never hardcoded
by `SQLRAGSystem.get_connection()` when it calls `psycopg2.connect(...)`,
so local (non-SSL) Postgres connections are not silently broken. This is
NOT a property of `_parse_connection` (`db_bootstrap.py`), which only
extracts `user`/`password`/`host`/`port`/`db_name` and never touches
`sslmode` at all — the regression this guards against lives specifically
in `get_connection()`'s call to `psycopg2.connect`.

#### Scenario: get_connection never passes a hardcoded sslmode kwarg

- GIVEN `DATABASE_URL` is set to a plain connection string with no `sslmode`
- WHEN `SQLRAGSystem.get_connection()` is called
- THEN `psycopg2.connect` is invoked with the connection string only, with no
  `sslmode` keyword argument added by the code

**Known gap (documented, not yet covered)**: no test currently asserts that
if a caller-supplied `DATABASE_URL` itself contains a `?sslmode=...` query
param, that value passes through untouched — `psycopg2.connect` receives the
full DSN string as-is, so this should hold by construction, but it is not
explicitly asserted. Low priority given `get_connection()` no longer adds
its own `sslmode` value.

### Requirement: Opt-In Integration Suite

The system MUST provide an integration test suite marked
`@pytest.mark.integration`, excluded from the default `pytest` run, that
exercises `db_bootstrap.ensure_app_database` idempotency and
`SQLProcessor.execute_sql` against real local Postgres/Neo4j containers.

#### Scenario: Default run excludes integration tests

- GIVEN the `integration` marker is registered in `pyproject.toml`
- WHEN a developer runs plain `pytest` with no `-m` filter
- THEN integration-marked tests are still collected but developers are
  expected to use `pytest -m "not integration"` for the offline-only run
- AND CI/local offline workflows explicitly pass `-m "not integration"`

#### Scenario: Integration suite run against local containers

- GIVEN `postgres-local` and `neo4j-local` containers are running
- WHEN a developer runs `pytest -m integration`
- THEN `ensure_app_database` can be called twice without duplicating rows or
  raising an error (idempotency)
- AND `SQLProcessor.execute_sql` successfully executes a `SELECT` against the
  real connection
