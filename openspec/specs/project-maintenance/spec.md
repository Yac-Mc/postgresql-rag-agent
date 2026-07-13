# Project Maintenance Specification

## Purpose

Keep the project's dependency manifest and source tree free of drift: every
import actually used by a script MUST be declared in `requirements.txt`, and
the codebase MUST NOT carry dead commented-out code that misleads readers
about the active code path.

## Requirements

### Requirement: Dependency Manifest Completeness

`requirements.txt` MUST declare every third-party package directly imported
by any script in the repository, pinned to the version installed in the
project's `venv`.

#### Scenario: Clean install runs vectorizador1.py

- GIVEN a fresh virtual environment
- WHEN a developer runs `pip install -r requirements.txt`
- THEN `python vectorizador1.py` MUST run without `ModuleNotFoundError` for
  `pandas` or `psutil`

#### Scenario: Version pin matches local environment

- GIVEN `pandas` and `psutil` are added to `requirements.txt`
- WHEN their pinned versions are compared to the versions installed in the
  project's `venv`
- THEN the pinned versions MUST match the installed versions

### Requirement: No Dead Commented-Out Code

Source files MUST NOT contain commented-out code blocks for functionality
that has been superseded by an active implementation.

#### Scenario: Superseded Neo4j search block removed

- GIVEN `graph.py` contains a commented-out `buscar_neo4j_completo` function
  and its associated commented `asyncio.to_thread` call site
- WHEN the dead code cleanup is applied
- THEN both the function block and its call site MUST be removed
- AND the active sync Neo4j search path MUST remain unchanged and functional

#### Scenario: Explanatory comments are preserved

- GIVEN `graph.py` contains genuine explanatory `#` comments unrelated to the
  removed dead code
- WHEN the dead code cleanup is applied
- THEN those explanatory comments MUST remain untouched
