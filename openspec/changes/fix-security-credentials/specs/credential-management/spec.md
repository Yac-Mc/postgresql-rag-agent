# Delta for credential-management

## ADDED Requirements

### Requirement: No Hardcoded Real Secrets
The system MUST NOT contain real credentials (host, user, password, API keys, connection strings) hardcoded as literal values in any tracked source file. All access to external services (Postgres, Neo4j, Gemini) MUST resolve credentials via environment variables, without a real-value fallback.

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

### Requirement: Secret-Scanning Pre-Commit Check
The project SHOULD provide a lightweight, non-blocking-aggressive script or pre-commit hook that scans tracked files for common hardcoded-secret patterns (connection strings with `user:pass@`, known API key formats) before a commit is created.

#### Scenario: Script flags a hardcoded connection string
- GIVEN a file contains a literal `postgresql://user:realpassword@host/db` pattern
- WHEN the secret-scanning script runs
- THEN it reports the file and line as a potential secret

#### Scenario: Script passes on env-based code
- GIVEN all DB/API access uses `os.getenv(...)` with no literal secrets
- WHEN the secret-scanning script runs
- THEN it reports no findings

### Requirement: Exposed Credential Rotation Documented
The project MUST document, in a user-facing location (README), that a real database password previously committed to git history (commit 6da906b) is compromised and requires manual rotation with the relevant provider, outside the scope of this codebase.

#### Scenario: README warns about exposed credential
- GIVEN a developer reads the README security section
- WHEN they reach the credentials/security note
- THEN they find explicit instructions to rotate the exposed password manually
- AND a reference to the git commit where it was exposed

## MODIFIED Requirements
None — no existing formal spec covered `SQLRAGSystem.get_connection()` or `vectorizador1.py` connection logic prior to this change.

## REMOVED Requirements
None.
