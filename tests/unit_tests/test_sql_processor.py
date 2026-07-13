"""Unit tests for `SQLProcessor` pure logic and `obtener_ddl_dinamico`."""
from unittest.mock import MagicMock, patch

from agent.graph import SQLProcessor, obtener_ddl_dinamico


class TestValidateSql:
    def setup_method(self):
        self.processor = SQLProcessor()

    def test_valid_select_query(self):
        result = self.processor.validate_sql("SELECT * FROM users")

        assert result == {"is_valid": True}

    def test_rejects_non_select_query(self):
        result = self.processor.validate_sql("UPDATE users SET name = 'x'")

        assert result["is_valid"] is False
        assert "SELECT" in result["error"]

    def test_rejects_dangerous_keyword_inside_select(self):
        result = self.processor.validate_sql(
            "SELECT * FROM users; DROP TABLE users;"
        )

        assert result["is_valid"] is False
        assert "DROP" in result["error"]

    def test_rejects_missing_from_clause(self):
        result = self.processor.validate_sql("SELECT 1")

        assert result["is_valid"] is False
        assert "FROM" in result["error"]


class TestParseSqlToAst:
    def setup_method(self):
        self.processor = SQLProcessor()

    def test_parses_select_from(self):
        ast = self.processor.parse_sql_to_ast("SELECT id, name FROM users")

        assert ast["select"] == ["id", "name"]
        assert ast["from"] == ["users"]

    def test_parses_join(self):
        ast = self.processor.parse_sql_to_ast(
            "SELECT u.id FROM users u JOIN orders o ON u.id = o.user_id"
        )

        assert ast["from"] == ["users u"]
        assert len(ast["join"]) == 1

    def test_returns_error_ast_on_invalid_input(self):
        ast = self.processor.parse_sql_to_ast(None)

        assert ast["intention"] == "error"
        assert "error" in ast


class TestObtenerDdlDinamico:
    def test_includes_table_and_column_names(self):
        mock_inspector = MagicMock()
        mock_inspector.get_table_names.return_value = ["users"]
        mock_inspector.get_pk_constraint.return_value = {"constrained_columns": ["id"]}
        mock_inspector.get_columns.return_value = [
            {"name": "id", "type": "INTEGER", "nullable": False},
            {"name": "email", "type": "VARCHAR", "nullable": True},
        ]
        mock_inspector.get_foreign_keys.return_value = []
        mock_engine = MagicMock()

        with patch("agent.sql_processing.inspect", return_value=mock_inspector):
            ddl = obtener_ddl_dinamico(mock_engine)

        assert "users" in ddl
        assert "email" in ddl
        assert "id" in ddl

    def test_reports_empty_schema(self):
        mock_inspector = MagicMock()
        mock_inspector.get_table_names.return_value = []
        mock_engine = MagicMock()

        with patch("agent.sql_processing.inspect", return_value=mock_inspector):
            ddl = obtener_ddl_dinamico(mock_engine)

        assert "No se encontraron tablas" in ddl
