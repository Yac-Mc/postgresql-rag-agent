"""Regression tests for `ChatbotConfig` connection configuration.

Covers two previously hand-verified bugs:
- Missing `DATABASE_URL` must raise `ValueError` from `_init_postgresql`.
- `sslmode` must never be hardcoded into the Postgres connection string
  built by `SQLRAGSystem.get_connection` (it must come from `DATABASE_URL`
  itself via a query param, if needed at all).
"""
from unittest.mock import MagicMock, patch

import pytest

from agent.graph import ChatbotConfig, SQLRAGSystem

pytestmark = pytest.mark.anyio


async def test_init_postgresql_raises_value_error_when_database_url_missing(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    config = ChatbotConfig()

    with pytest.raises(ValueError, match="DATABASE_URL"):
        await config._init_postgresql()


def test_get_connection_never_hardcodes_sslmode(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/db")
    rag_system = SQLRAGSystem.__new__(SQLRAGSystem)

    with patch("agent.graph.psycopg2.connect", return_value=MagicMock()) as mock_connect:
        rag_system.get_connection()

    mock_connect.assert_called_once_with("postgresql://user:pass@localhost:5432/db")
    _, kwargs = mock_connect.call_args
    assert "sslmode" not in kwargs
