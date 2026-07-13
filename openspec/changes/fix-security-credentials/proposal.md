# Proposal: fix-security-credentials

## Intent
Fase 1 removió 4 secretos de src/agent/graph.py, pero una revisión formal encontró **2 secretos reales adicionales que sobrevivieron**: credenciales completas de un proveedor externo (host, usuario, password reales) hardcodeadas en `SQLRAGSystem.get_connection()` (graph.py:566-574) y duplicadas como connection string completo en `vectorizador1.py:40`. Esta password ya está en el historial de git (commit 6da906b) y potencialmente expuesta si el repo se hace público. Objetivo: eliminar todo secreto hardcodeado remanente y agregar una salvaguarda mínima (no sobre-ingenierizada) para prevenir reintroducción.

## Scope
### In Scope
- Reemplazar credenciales hardcodeadas en graph.py (SQLRAGSystem) y vectorizador1.py por os.getenv(), reusando DATABASE_URL existente
- Auditar el resto de archivos trackeados (api.py, db_bootstrap.py, wsgi.py, __init__.py, requirements.txt, pyproject.toml, langgraph.json, README.md, .env.example) — auditoría ya realizada vía git grep, sin otros hallazgos
- Documentar en README que la password expuesta en el historial de git debe rotarse (acción manual del usuario, fuera del repo)
- Agregar un chequeo simple de secret-scanning liviano, sin CI/CD complejo

### Out of Scope
- Reescritura de historial de git (git filter-repo / BFG) — se documenta como recomendación, no se ejecuta automáticamente
- Rotacion real de la credencial expuesta (acción del usuario en su dashboard)
- Infraestructura de secret management (Vault, AWS Secrets Manager, etc.) — sobre-ingeniería para proyecto de tesis

## Capabilities
### New Capabilities
- `secret-scanning-check`: chequeo simple (script) que detecta patrones de credenciales hardcodeadas antes de commitear

### Modified Capabilities
- None (graph.py y vectorizador1.py no exponen una "capability" de spec formal, es remediación interna de seguridad)

## Approach
1. Reusar `DATABASE_URL` (ya existente y validado en `ChatbotConfig`) en vez de introducir una variable nueva
2. Actualizar `SQLRAGSystem.get_connection()` y `vectorizador1.py` para leer desde os.getenv() sin fallback real, con error explícito si falta
3. Agregar script simple `scripts/check_secrets.py` con regex para patrones comunes (connection strings con user:pass@, API keys conocidas)
4. Documentar en README.md la necesidad de rotar la password expuesta en el historial

## Affected Areas
| Area | Impact | Description |
|------|--------|-------------|
| `src/agent/graph.py` | Modified | SQLRAGSystem.get_connection() usa os.getenv() |
| `src/agent/vectorizador1.py` | Modified | DB_URL usa os.getenv() |
| `README.md` | Modified | Nota de rotación de credencial + política de secretos |
| `scripts/` (nuevo) | New | Chequeo simple de secret-scanning |

## Risks
| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Password ya expuesta en historial de git | High | Documentar necesidad de rotación manual; no se puede remediar solo con este cambio |
| Falta de var de entorno rompe conexión en runtime | Med | Validar con error explícito temprano, igual que NEO4J_PASSWORD |
| Secret-scanning con falsos positivos | Low | Mantener el chequeo simple (regex acotado), no bloqueante agresivo |

## Rollback Plan
Revertir el commit del cambio; los valores hardcodeados no se reintroducen automáticamente porque el fallback real fue removido intencionalmente (no hay regresión funcional posible sin código previo).

## Dependencies
- Ninguna externa. Requiere que el usuario rote la password expuesta manualmente en el proveedor correspondiente (fuera del alcance del código)

## Success Criteria
- [x] No quedan credenciales/secretos reales hardcodeados en ningún archivo trackeado del repo
- [x] graph.py y vectorizador1.py leen credenciales desde variables de entorno
- [x] .env.example documenta todas las vars necesarias
- [x] Existe un chequeo simple de secret-scanning (script)
- [x] README.md advierte sobre la rotación pendiente de la password expuesta en el historial
