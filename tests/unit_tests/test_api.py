"""Unit tests for the `POST /chat` endpoint in `agent.api`.

Verifies the HTTP status mapping introduced by the LLM error classification:
transient LLM errors map to 503, while security rejections and other
existing errors keep the current 400 behavior.
"""
import os
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from agent.api import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_headers():
    return {"X-API-Key": os.environ["API_KEY"]}


def test_chat_returns_503_on_transient_llm_error(client, auth_headers):
    app.state.graph.ainvoke = AsyncMock(
        return_value={
            "errores": ["Error en análisis de seguridad: rate limited"],
            "metadata": {"error_tipo": "error_transitorio"},
        }
    )

    response = client.post(
        "/chat", json={"texto": "cuantos usuarios hay activos"}, headers=auth_headers
    )

    assert response.status_code == 503


def test_chat_returns_400_on_security_rejection(client, auth_headers):
    app.state.graph.ainvoke = AsyncMock(
        return_value={
            "errores": ["Pregunta rechazada por seguridad: contiene operaciones peligrosas"],
            "metadata": {"error_tipo": "rechazo_seguridad"},
        }
    )

    response = client.post(
        "/chat", json={"texto": "eliminar la tabla de usuarios"}, headers=auth_headers
    )

    assert response.status_code == 400


def test_chat_returns_400_when_error_tipo_missing(client, auth_headers):
    app.state.graph.ainvoke = AsyncMock(
        return_value={
            "errores": ["Error de ejecución SQL"],
            "metadata": {},
        }
    )

    response = client.post(
        "/chat", json={"texto": "cuantos usuarios hay activos"}, headers=auth_headers
    )

    assert response.status_code == 400


def test_chat_returns_401_without_api_key_header(client):
    response = client.post("/chat", json={"texto": "cuantos usuarios hay activos"})

    assert response.status_code == 401


def test_chat_returns_401_with_incorrect_api_key(client):
    response = client.post(
        "/chat",
        json={"texto": "cuantos usuarios hay activos"},
        headers={"X-API-Key": "wrong-key"},
    )

    assert response.status_code == 401
    assert os.environ["API_KEY"] not in response.text


def test_chat_returns_200_with_correct_api_key(client):
    app.state.graph.ainvoke = AsyncMock(
        return_value={
            "errores": [],
            "respuesta_natural": "respuesta ok",
            "metadata": {},
        }
    )

    response = client.post(
        "/chat",
        json={"texto": "cuantos usuarios hay activos"},
        headers={"X-API-Key": os.environ["API_KEY"]},
    )

    assert response.status_code == 200
