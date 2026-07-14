# Proposal: add-api-key-auth

## Intent
La API REST (`src/agent/api.py`) no tiene ningun mecanismo de autenticacion: cualquiera que conozca la URL puede invocar `POST /chat` sin restriccion. Esto es aceptable en desarrollo local, pero se vuelve un riesgo real como paso previo a un despliegue publico (Render u otro PaaS free tier, de caracter temporal). Objetivo: agregar autenticacion simple por API Key antes de exponer el servicio fuera de localhost, sin sobre-ingenieria (sin rate limiting, CORS, ni manejo especial de stack traces en esta fase).

## Scope

### In Scope
- Nueva variable de entorno obligatoria `API_KEY` (analoga a `GEMINI_API_KEY`/`NEO4J_PASSWORD` en `.env`), sin fallback de valor real
- Validacion del header `X-API-Key` en `POST /chat` usando `fastapi.security.APIKeyHeader` + `Security()` (no `Header()` manual)
- Respuesta `401 Unauthorized` cuando el header falta o la key no coincide, sin filtrar el valor esperado en el mensaje de error
- Boton "Authorize" visible y funcional en Swagger UI (`/docs`), permitiendo probar el endpoint ingresando la key ahi
- Patron reutilizable para futuros endpoints que requieran el mismo esquema de seguridad
- Actualizacion de README: seccion "Roadmap - Nuevas funcionalidades" pasa a usar checkboxes markdown (`- [ ]` / `- [x]`), aplicado tanto al item existente `add-conversation-memory` (pendiente) como al nuevo `add-api-key-auth` (se marca al archivar). La edicion real del README ocurre en fases posteriores (apply/archive), no en esta propuesta

### Out of Scope
- Rate limiting
- CORS
- Ocultar stack traces / mensajes de error genericos ante excepciones no relacionadas con auth (ya cubierto parcialmente por manejo de errores existente en `api.py`)
- Rotacion automatica o gestion multi-key (una sola key global por ahora)
- Autenticacion de usuarios finales (OAuth, JWT, sesiones)

## Capabilities

### New Capabilities
- `api-authentication`: autenticacion por API Key vía header `X-API-Key` para endpoints de la API REST, con esquema de seguridad OpenAPI (`APIKeyHeader`) que habilita el boton Authorize en Swagger UI

### Modified Capabilities
- `credential-management`: se agrega `API_KEY` como nueva variable de entorno obligatoria, siguiendo el mismo patron sin fallback de valor real que ya aplica a `DATABASE_URL`/`GEMINI_API_KEY`/`NEO4J_PASSWORD`

## Approach
1. Definir un esquema de seguridad `APIKeyHeader(name="X-API-Key", auto_error=False)` de `fastapi.security` en `api.py`
2. Crear una dependencia (`Security(...)`) que compare el valor recibido contra `os.getenv("API_KEY")`; si falta la env var al arrancar, fallar rapido (mismo patron que credenciales existentes)
3. Si el header falta o no coincide, levantar `HTTPException(401)` con mensaje generico, sin exponer la key esperada
4. Aplicar la dependencia a `POST /chat` vía parametro de la ruta (no middleware global), para que quede explicito por endpoint y sea facil de replicar en endpoints futuros
5. Verificar manualmente que `/docs` muestra el boton "Authorize" y que autenticar ahi permite invocar `/chat` con éxito
6. Documentar `API_KEY` en `.env.example` (o equivalente) y en README

## Affected Areas
| Area | Impact | Description |
|------|--------|-------------|
| `src/agent/api.py` (`POST /chat`) | Modified | Agrega dependencia de seguridad `APIKeyHeader` + validacion contra `API_KEY`; respuesta 401 ante falta/incorrecta |
| `.env` / `.env.example` | Modified | Nueva variable `API_KEY` obligatoria, sin valor real hardcodeado |
| `README.md` (Roadmap) | Modified | Checkboxes markdown para `add-conversation-memory` (pendiente) y `add-api-key-auth` (pendiente, se marca al archivar) |

## Risks
| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Olvidar setear `API_KEY` en el entorno de despliegue rompe el arranque o deja el endpoint sin proteger | Med | Fallar rapido si `API_KEY` no esta seteada (mismo patron que otras credenciales), nunca usar valor por defecto |
| Mensaje de error 401 filtra accidentalmente la key esperada o pistas de su formato | Low | Mensaje generico fijo, sin interpolar el valor recibido ni el esperado |
| `Header()` manual usado por error en vez de `APIKeyHeader`/`Security()`, y el boton Authorize no aparece en `/docs` | Med | Verificacion manual explicita de `/docs` como criterio de aceptacion antes de archivar |

## Rollback Plan
Revertir el commit del cambio; `api.py` vuelve a aceptar requests sin autenticacion (comportamiento actual). No hay migracion de datos ni estado persistente involucrado; solo requiere remover `API_KEY` de la configuracion de entorno si ya se habia seteado en despliegue.

## Dependencies
- Ninguna externa nueva (`fastapi.security` ya forma parte de FastAPI, sin agregar dependencias al proyecto)

## Success Criteria
- [ ] `POST /chat` sin header `X-API-Key` devuelve 401
- [ ] `POST /chat` con `X-API-Key` incorrecta devuelve 401, sin filtrar la key esperada en el mensaje
- [ ] `POST /chat` con `X-API-Key` correcta responde normalmente (comportamiento actual sin cambios)
- [ ] Swagger UI (`/docs`) muestra el boton "Authorize" y permite probar el endpoint ingresando la key ahi
- [ ] README Roadmap usa checkboxes markdown, con `add-api-key-auth` marcado al archivar este cambio
