# Proposal: distinguish-llm-transient-errors

## Intent
En `analizar_seguridad` (graph.py:429-474), cualquier excepcion al invocar Gemini (429 rate-limit, timeout, error de red) cae en el mismo `except Exception` que trata rechazos reales de seguridad, devolviendo el mensaje "no puedo procesar tu solicitud por seguridad" aunque la pregunta nunca fue evaluada. Esto ya confundio al usuario en sesion previa (cuota de Gemini agotada = mensaje de rechazo por seguridad). Objetivo: distinguir rechazo real de seguridad vs. error transitorio del LLM, y comunicarlo honestamente en HTTP status/mensaje (api.py) y en `respuesta_natural` (stream_response/LangGraph Studio).

## Scope

### In Scope
- Nuevo campo de clasificacion en `decision_seguridad` (graph.py) que distinga: rechazo real de seguridad vs. error transitorio del LLM
- Manejo separado de excepciones de invocacion del LLM (429/timeout/red) vs. rechazo explicito `es_segura: false`
- Tratamiento de `json.JSONDecodeError` como error transitorio/de formato, NO como rechazo de seguridad (ver decision abajo)
- Propagacion de la distincion hasta `api.py`: rechazo de seguridad -> 400, error transitorio -> 503 con mensaje de "servicio no disponible, reintenta en unos momentos"
- Mensaje honesto en `respuesta_natural` para el path de `stream_response`/LangGraph Studio (no hay HTTPException ahi)

### Out of Scope
- Deteccion de palabras peligrosas por regex (graph.py:375-402) - ya funciona bien, no es ambigua
- `manejar_respuesta_llm` - no se toca salvo lo estrictamente necesario para propagar el nuevo campo
- Reintentos automaticos (retry/backoff) - es funcionalidad de resiliencia nueva, fuera de esta fase
- Cambio de modelo de Gemini

## Capabilities

### New Capabilities
- `llm-error-classification`: clasificacion del resultado de `analizar_seguridad` en rechazo real de seguridad vs. error transitorio del proveedor LLM, y su propagacion honesta a HTTP status y mensajes de usuario

### Modified Capabilities
- None (no hay spec formal existente para el nodo de analisis de seguridad; se cubre con la nueva capability)

## Approach
1. Agregar campo `tipo` a `decision_seguridad` (ej. `"rechazo_seguridad" | "error_transitorio"`) - diseno exacto de campos se define en design.md
2. Separar el bloque `try/except` actual en dos responsabilidades: (a) excepciones de invocacion del LLM (candidatas: `ResourceExhausted`, `DeadlineExceeded`, `ServiceUnavailable` de `google.api_core.exceptions`, o errores de red estandar) -> `tipo: error_transitorio`; (b) `es_segura: false` explicito del LLM -> `tipo: rechazo_seguridad`. Enumeracion exacta de excepciones a capturar se resuelve en design.md
3. **Decision propuesta**: `json.JSONDecodeError` (LLM respondio pero no en formato valido) se clasifica como `error_transitorio`, no como rechazo de seguridad - no hay evidencia de que la pregunta sea insegura, solo un problema de formato de respuesta. **Pendiente de confirmacion con el usuario** (ver resumen final)
4. En `api.py`, leer `tipo` desde el resultado y mapear: `rechazo_seguridad` -> 400 (comportamiento actual), `error_transitorio` -> 503 con mensaje generico de disponibilidad
5. En el path de `stream_response`, el nuevo caso de error transitorio setea su propio `respuesta_natural` honesto, sin pasar por `rechazar_pregunta` (que queda exclusivo para rechazo real)

## Affected Areas
| Area | Impact | Description |
|------|--------|-------------|
| `src/agent/graph.py` (`analizar_seguridad`, ~429-474) | Modified | Separar excepciones transitorias de rechazo real; nuevo campo `tipo` en `decision_seguridad` |
| `src/agent/graph.py` (`rechazar_pregunta`, ~993-1015) | Modified | Queda exclusivo para rechazo real; nuevo camino para error transitorio no pasa por aca |
| `src/agent/graph.py` (`stream_response`, ~270-328) | Modified | Mensaje honesto en `respuesta_natural` segun `tipo` |
| `src/agent/api.py` (POST /chat, 33-61) | Modified | Mapear `tipo` a status code: 400 (rechazo) vs. 503 (transitorio) |

## Risks
| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Excepcion del SDK no matchea ninguna clase esperada y cae a un default incorrecto | Med | Definir un `except Exception` residual explicito con `tipo: error_transitorio` como fallback conservador (nunca asumir rechazo de seguridad ante excepcion no reconocida) |
| Cambio de status code (400->503) rompe un cliente que solo chequea 400 | Low | Documentar el cambio en README/CHANGELOG; es el comportamiento correcto esperado |
| JSON invalido mal clasificado genera falsa sensacion de disponibilidad cuando en realidad el LLM respondio mal reiteradamente | Low | Mensaje de error transitorio ya sugiere "reintenta", cubre ambos casos sin falsa seguridad |

## Rollback Plan
Revertir el commit del cambio; el comportamiento vuelve a tratar toda excepcion como rechazo de seguridad (comportamiento actual, funcional pero confuso). No hay migracion de datos ni estado persistente involucrado.

## Dependencies
- Ninguna externa. Requiere confirmar en design.md que excepciones concretas de `google.api_core.exceptions` (o de red) se consideran transitorias

## Success Criteria
- [ ] Un 429/timeout/error de red de Gemini en `analizar_seguridad` nunca genera el mensaje de "rechazo por seguridad"
- [ ] api.py devuelve 503 (no 400) ante error transitorio del LLM, con mensaje distinto al de rechazo
- [ ] El path de `stream_response`/LangGraph Studio refleja la misma distincion en `respuesta_natural`
- [ ] Rechazo real de seguridad (`es_segura: false` o palabras peligrosas por regex) sigue devolviendo 400 sin cambios de comportamiento
- [ ] JSON invalido del LLM se trata como error transitorio, no como rechazo (decision confirmada con el usuario)
