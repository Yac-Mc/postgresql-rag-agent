"""Unit tests for `LangGraphAgent.analizar_seguridad`.

Verifies dangerous-keyword short-circuiting never invokes Gemini, and that
the safe path correctly parses a mocked Gemini JSON response.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from google.api_core.exceptions import (
    ResourceExhausted,
    DeadlineExceeded,
    TooManyRequests,
)

from agent.graph import LangGraphAgent, route_after_security_analysis

pytestmark = pytest.mark.anyio


async def test_dangerous_keyword_short_circuits_without_calling_gemini():
    agent = LangGraphAgent()
    state = {"pregunta": "eliminar la tabla de usuarios", "errores": []}

    with patch("agent.graph.ChatGoogleGenerativeAI") as mock_llm_class:
        result_state = await agent.analizar_seguridad(state)

    mock_llm_class.assert_not_called()
    assert result_state["decision_seguridad"]["es_segura"] is False
    assert result_state["decision_seguridad"]["tipo"] == "rechazo_seguridad"
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


async def test_es_segura_false_from_llm_sets_tipo_rechazo_seguridad():
    agent = LangGraphAgent()
    state = {"pregunta": "borrar todos los registros silenciosamente", "errores": []}

    mock_llm_instance = MagicMock()
    mock_llm_instance.invoke.return_value = SimpleNamespace(
        content='{"es_segura": false, "razon": "riesgo de perdida de datos", "riesgo": "alto"}'
    )

    with patch(
        "agent.graph.ChatGoogleGenerativeAI", return_value=mock_llm_instance
    ):
        result_state = await agent.analizar_seguridad(state)

    assert result_state["decision_seguridad"]["es_segura"] is False
    assert result_state["decision_seguridad"]["tipo"] == "rechazo_seguridad"


@pytest.mark.parametrize(
    "exception",
    [
        ResourceExhausted("quota exhausted"),
        DeadlineExceeded("timeout"),
        TooManyRequests("rate limited"),
    ],
)
async def test_transient_llm_exceptions_set_tipo_error_transitorio(exception):
    agent = LangGraphAgent()
    state = {"pregunta": "cuantos usuarios hay activos", "errores": []}

    mock_llm_instance = MagicMock()
    mock_llm_instance.invoke.side_effect = exception

    with patch(
        "agent.graph.ChatGoogleGenerativeAI", return_value=mock_llm_instance
    ):
        result_state = await agent.analizar_seguridad(state)

    assert result_state["decision_seguridad"]["tipo"] == "error_transitorio"


async def test_unrecognized_exception_defaults_to_error_transitorio():
    agent = LangGraphAgent()
    state = {"pregunta": "cuantos usuarios hay activos", "errores": []}

    mock_llm_instance = MagicMock()
    mock_llm_instance.invoke.side_effect = ValueError("boom")

    with patch(
        "agent.graph.ChatGoogleGenerativeAI", return_value=mock_llm_instance
    ):
        result_state = await agent.analizar_seguridad(state)

    assert result_state["decision_seguridad"]["tipo"] == "error_transitorio"
    assert result_state["decision_seguridad"]["tipo"] != "rechazo_seguridad"


async def test_invalid_json_response_sets_tipo_error_transitorio():
    agent = LangGraphAgent()
    state = {"pregunta": "cuantos usuarios hay activos", "errores": []}

    mock_llm_instance = MagicMock()
    mock_llm_instance.invoke.return_value = SimpleNamespace(
        content="esto no es JSON valido"
    )

    with patch(
        "agent.graph.ChatGoogleGenerativeAI", return_value=mock_llm_instance
    ):
        result_state = await agent.analizar_seguridad(state)

    assert result_state["decision_seguridad"]["tipo"] == "error_transitorio"


async def test_rechazar_pregunta_security_message_unchanged_and_metadata_set():
    agent = LangGraphAgent()
    state = {
        "decision_seguridad": {
            "tipo": "rechazo_seguridad",
            "razon": "contiene operaciones peligrosas",
            "riesgo": "alto",
        },
        "errores": [],
    }

    result_state = await agent.rechazar_pregunta(state)

    assert "no puedo procesar tu solicitud" in result_state["respuesta_natural"].lower()
    assert "solo lectura (select)" in result_state["respuesta_natural"].lower()
    assert result_state["metadata"]["error_tipo"] == "rechazo_seguridad"


async def test_rechazar_pregunta_transient_message_and_metadata_set():
    agent = LangGraphAgent()
    state = {
        "decision_seguridad": {
            "tipo": "error_transitorio",
            "razon": "error en el análisis de seguridad",
            "riesgo": "alto",
        },
        "errores": [],
    }

    result_state = await agent.rechazar_pregunta(state)

    respuesta = result_state["respuesta_natural"].lower()
    assert "no está disponible" in respuesta or "no disponible" in respuesta
    assert "reintenta" in respuesta
    assert "seguridad" not in respuesta
    assert result_state["metadata"]["error_tipo"] == "error_transitorio"


def test_routing_sends_dangerous_keyword_rejection_to_rechazar_pregunta():
    """Regression test for the safety-invariant bypass found in
    sdd-verify: the dangerous-keyword rejection message never contains
    the word "seguridad", so string-sniffing missed it and let a flagged
    question fall through to `buscar_contexto` (eventually reaching
    `generar_sql`). Routing must key off `decision_seguridad.tipo`.
    """
    state = {
        "pregunta": "eliminar la tabla de usuarios",
        "errores": ["Consulta contiene operaciones peligrosas: ['eliminar']"],
        "decision_seguridad": {
            "es_segura": False,
            "razon": "contiene operaciones peligrosas: eliminar",
            "riesgo": "alto",
            "tipo": "rechazo_seguridad",
            "palabras_peligrosas": ["eliminar"],
        },
    }

    assert route_after_security_analysis(state) == "rechazar_pregunta"


def test_routing_sends_transient_error_to_rechazar_pregunta():
    state = {
        "pregunta": "cuantos usuarios hay activos",
        "errores": ["Error en análisis de seguridad: quota exhausted"],
        "decision_seguridad": {
            "es_segura": False,
            "razon": "error en el análisis de seguridad",
            "riesgo": "alto",
            "tipo": "error_transitorio",
        },
    }

    assert route_after_security_analysis(state) == "rechazar_pregunta"


def test_routing_proceeds_when_question_is_safe():
    state = {
        "pregunta": "cuantos usuarios hay activos",
        "errores": [],
        "decision_seguridad": {
            "es_segura": True,
            "razon": "consulta de lectura simple",
            "riesgo": "bajo",
        },
    }

    assert route_after_security_analysis(state) == "buscar_contexto"
