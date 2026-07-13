"""Opt-in integration tests against real local Postgres/Neo4j Docker containers.

Excluded by default (`addopts` runs the full suite; use
`pytest -m "not integration"` to skip, or `pytest -m integration` to run
only these). Requires `postgres-local` and `neo4j-local` Docker containers
running locally, and a real `DATABASE_URL` pointing at them (loaded via the
project's `.env`, not hardcoded here).
"""
import os

import psycopg2
import pytest
from dotenv import load_dotenv
from sqlalchemy import create_engine

load_dotenv()

from agent import db_bootstrap  # noqa: E402
from agent.graph import ChatbotConfig, SQLProcessor  # noqa: E402

pytestmark = pytest.mark.integration


def test_ensure_app_database_is_idempotent():
    """Running the bootstrap twice must not duplicate seeded rows."""
    conn_params, db_name = db_bootstrap._parse_connection(os.environ["DATABASE_URL"])
    table_name = os.environ.get("APP_DB_TABLE", "Usuario")

    db_bootstrap.ensure_app_database()

    with psycopg2.connect(**conn_params, dbname=db_name) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(f'SELECT COUNT(*) FROM "{table_name}"')
            count_after_first_run = cur.fetchone()[0]

    db_bootstrap.ensure_app_database()

    with psycopg2.connect(**conn_params, dbname=db_name) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(f'SELECT COUNT(*) FROM "{table_name}"')
            count_after_second_run = cur.fetchone()[0]

    assert count_after_first_run == count_after_second_run


def test_execute_sql_against_real_connection():
    config = ChatbotConfig.__new__(ChatbotConfig)
    config.engine = create_engine(os.environ["DATABASE_URL"])

    processor = SQLProcessor(config=config)
    results, error = processor.execute_sql("SELECT 1 AS uno")

    assert error is None
    assert results == [{"uno": 1}]
