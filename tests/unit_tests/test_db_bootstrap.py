"""Unit tests for `db_bootstrap._parse_connection` (pure parsing, no I/O)."""
from agent.db_bootstrap import _parse_connection


def test_parses_full_connection_string():
    conn_params, db_name = _parse_connection(
        "postgresql://myuser:mypass@localhost:5432/MyDatabase"
    )

    assert conn_params == {
        "user": "myuser",
        "password": "mypass",
        "host": "localhost",
        "port": 5432,
    }
    assert db_name == "MyDatabase"


def test_defaults_port_when_missing():
    conn_params, _ = _parse_connection("postgresql://user:pass@localhost/db")

    assert conn_params["port"] == 5432


def test_empty_path_yields_empty_db_name():
    _, db_name = _parse_connection("postgresql://user:pass@localhost:5432/")

    assert db_name == ""


def test_preserves_db_name_case():
    _, db_name = _parse_connection(
        "postgresql://user:pass@localhost:5432/Usuarios"
    )

    assert db_name == "Usuarios"
