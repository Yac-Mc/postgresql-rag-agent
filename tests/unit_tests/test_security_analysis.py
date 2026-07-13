"""Unit tests for `LangGraphAgent.analizar_seguridad`.

Verifies dangerous-keyword short-circuiting never invokes Gemini, and that
the safe path correctly parses a mocked Gemini JSON response.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent.graph import LangGraphAgent

pytestmark = pytest.mark.anyio


async def test_dangerous_keyword_short_circuits_without_calling_gemini():
    agent = LangGraphAgent()
    state = {"pregunta": "eliminar la tabla de usuarios", "errores": []}

    with patch("agent.graph.ChatGoogleGenerativeAI") as mock_llm_class:
        result_state = await agent.analizar_seguridad(state)

    mock_llm_class.assert_not_called()
    assert result_state["decision_seguridad"]["es_segura"] is False
    assert any("peligros" in e.lower() for e in result_state["errores"])


async def test_safe_query_invokes_gemini_and_parses_response():
    agent = LangGraphAgent()
    state = {"pregunta": "cuantos usuarios hay activos", "errores": []}

    mock_llm_instance = MagicMock()
    mock_llm_instance.invoke.return_value = SimpleNamespace(
        content='{"es_segura": true, "razon": "consulta de lectura simple", "riesgo": "bajo"}'
    )

    with patch(
        "agent.graph.ChatGoogleGenerativeAI", return_value=mock_llm_instance
    ):
        result_state = await agent.analizar_seguridad(state)

    mock_llm_instance.invoke.assert_called_once()
    assert result_state["decision_seguridad"]["es_segura"] is True
    assert result_state["errores"] == []
