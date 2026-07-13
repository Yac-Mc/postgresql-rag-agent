import os
from unittest.mock import MagicMock, patch

import pytest

# Tracks every Patcher object started at module level below so the
# `_stop_module_patches` fixture can tear them all down before other test
# packages (e.g. tests/integration_tests/) run in the same process.
_active_patches = []

# Module-level (NOT inside a fixture function): runs when conftest.py itself
# is imported, which pytest does before collecting sibling test files -
# guaranteed to run before `agent.graph`'s import-time setup_graph() fires.
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/testdb")
os.environ.setdefault("NEO4J_PASSWORD", "test")
os.environ.setdefault("GEMINI_API_KEY", "test-key-not-real")

# --- External-library patches, applied BEFORE any `agent.*` import ---
#
# Design deviation from design.md: `patch("agent.db_bootstrap.ensure_app_database")`
# internally calls `importlib.import_module("agent.db_bootstrap")`, which
# transitively imports `agent` -> `agent.graph`, executing the real
# module-level `agent = setup_graph()` BEFORE the patch is ever applied.
# String-path patches under `agent.*` cannot neutralize side effects that
# happen as a side effect of resolving the patch target itself.
#
# Fix: patch the external I/O libraries directly (by object reference, not
# string path) first, so that by the time the first `agent.*` patch below
# triggers the real import, Postgres/SQLAlchemy/Neo4j/the embeddings model
# are already mocked and the real `setup_graph()` runs fully offline.
import psycopg2  # noqa: E402
import sqlalchemy  # noqa: E402
import sentence_transformers  # noqa: E402
from neo4j import GraphDatabase  # noqa: E402

_active_patches.append(
    patch.object(psycopg2, "connect", return_value=MagicMock()).start()
)
_active_patches.append(
    patch.object(sqlalchemy, "create_engine", return_value=MagicMock()).start()
)

# `obtener_ddl_dinamico` calls `inspect(engine)` at module-import time
# (inside `_init_postgresql`). A generic `MagicMock` engine isn't a type
# SQLAlchemy's `inspect()` recognizes, so patch `inspect` itself to return a
# fake inspector reporting an empty schema - safe and consistent with the
# `patch("agent.sql_processing.inspect", ...)` pattern used in
# test_sql_processor.py for the same function (post module-split; that
# function now lives in agent/sql_processing.py, not agent/graph.py).
_fake_inspector = MagicMock()
_fake_inspector.get_table_names.return_value = []
_active_patches.append(
    patch.object(sqlalchemy, "inspect", return_value=_fake_inspector).start()
)

_active_patches.append(
    patch.object(GraphDatabase, "driver", return_value=MagicMock()).start()
)
_active_patches.append(
    patch.object(
        sentence_transformers, "SentenceTransformer", return_value=MagicMock()
    ).start()
)

# --- Defense-in-depth patches at the `agent.*` level (per design.md intent) ---
# By this point the first import of `agent.graph` (triggered by resolving
# the patch target below) is safe: Postgres/SQLAlchemy/Neo4j/the embeddings
# model are already mocked above.
_bootstrap_patch = patch("agent.db_bootstrap.ensure_app_database")
_active_patches.append(_bootstrap_patch.start())

_neo4j_patch = patch("agent.config.Neo4jGraph")
_active_patches.append(_neo4j_patch.start())

_postgres_verify_patch = patch(
    "agent.graph.ChatbotConfig.verificar_conexion_postgresql",
    return_value=True,
)
_active_patches.append(_postgres_verify_patch.start())


@pytest.fixture(scope="package", autouse=True)
def _stop_module_patches():
    """Stop every module-level patch after the unit test package finishes.

    Prevents the mocks above from leaking into other test packages (e.g.
    tests/integration_tests/) when the full suite runs in one pytest
    process. Uses `patch.stopall()` (idempotent, stops in reverse start
    order) instead of manually iterating `_active_patches` - see design.md.
    """
    yield
    patch.stopall()
