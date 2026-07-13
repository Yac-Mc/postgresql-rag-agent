# postgresql-rag-agent

Agente conversacional que traduce preguntas en lenguaje natural a consultas SQL
sobre **cualquier base de datos PostgreSQL**, ejecuta la consulta y devuelve la
respuesta en lenguaje natural. Usa RAG (ChromaDB) para reutilizar consultas
similares ya resueltas y Neo4j para indexar el historial de consultas SQL
generadas.

No está atado a ningún dominio de negocio: el esquema de la base de datos se
descubre en tiempo de ejecución por introspección (`information_schema`), no
está hardcodeado.

## Arquitectura (resumen)

- **LangGraph**: orquesta el flujo como un grafo de nodos (análisis de
  seguridad → búsqueda de contexto RAG → generación de SQL → validación →
  ejecución → generación de respuesta en lenguaje natural).
- **Gemini** (`langchain-google-genai`): LLM usado para análisis de seguridad,
  generación de SQL y redacción de la respuesta final.
- **PostgreSQL**: la base de datos objetivo sobre la que se ejecutan las
  consultas generadas.
- **ChromaDB**: vector store local para RAG (búsqueda de consultas similares).
- **Neo4j**: grafo para indexar el historial de consultas SQL ejecutadas.
- **FastAPI**: expone el agente como API (`POST /chat`), alternativa a correrlo
  con `langgraph dev` (LangGraph Studio).

## Stack tecnológico

| Tecnología                  | Uso en el proyecto                                                  |
|------------------------------|----------------------------------------------------------------------|
| Python 3.11+                 | Lenguaje del proyecto                                                |
| LangGraph                    | Orquestación del flujo del agente como grafo de estados             |
| LangChain + `langchain-google-genai` | Integración con Gemini (LLM)                                 |
| FastAPI + Uvicorn             | Expone el agente como API REST (`/chat`)                            |
| SQLAlchemy + psycopg2         | Conexión e introspección de esquema sobre PostgreSQL                |
| ChromaDB + sentence-transformers | Vector store local y embeddings para RAG                        |
| Neo4j (driver + `langchain-neo4j`) | Grafo para indexar el historial de consultas SQL generadas     |
| Docker                        | Levanta Postgres y Neo4j en local (no se instalan nativamente)      |

## Requisitos

- Python 3.11+ (ya instalado)
- Docker Desktop instalado **y corriendo** (para levantar Postgres y Neo4j en local)
- Una API key de [Google AI Studio](https://aistudio.google.com/apikey) (Gemini)

## Setup local (Windows / PowerShell)

### 1. Levantar Postgres y Neo4j con Docker

Asegurate de que Docker Desktop esté abierto y corriendo antes de este paso.

```powershell
docker run --name postgres-local -e POSTGRES_PASSWORD=postgres -p 5432:5432 -d postgres:16
docker run --name neo4j-local -e NEO4J_AUTH=neo4j/tu_password -p 7474:7474 -p 7687:7687 -d neo4j:5.26
```

(Si ya tenés estos contenedores corriendo de antes, no hace falta recrearlos.)

### 2. Crear y activar el entorno virtual

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

### 3. Instalar dependencias

```powershell
pip install -r requirements.txt
pip install -e .
```

### 4. Configurar variables de entorno

Copiá `.env.example` a `.env` y completá los valores (especialmente
`NEO4J_PASSWORD` y `GEMINI_API_KEY`):

```powershell
Copy-Item .env.example .env
```

`DATABASE_URL` debe incluir el nombre de la base de datos final. Si esa base
de datos no existe todavía, el bootstrap la crea automáticamente al arrancar
(junto con una tabla de demo `Usuario` con 5 filas de ejemplo), para poder
probar el flujo NL→SQL sin tener una base de datos propia todavía.

### 5. Correr el agente

**Opción A — LangGraph Studio (modo gráfico):**

```powershell
langgraph dev --no-reload --tunnel
```

> **`--no-reload`**: sin él, `langgraph dev` detecta las escrituras
> constantes que hace ChromaDB en `chroma_db_v2/` como "cambios de código" y
> reinicia el proceso en loop, hasta terminar cayéndose. Es un fix de
> desarrollo local; no aplica al modo API (`api.py`), que no tiene hot-reload.
>
> **`--tunnel`**: sin él, la UI de LangGraph Studio (hosteada en
> `smith.langchain.com`) no puede conectarse al servidor local — el
> navegador bloquea el acceso a `http://127.0.0.1` desde un sitio HTTPS
> externo ("Private Network Access"), mostrando `Failed to fetch`. El flag
> crea un túnel público de Cloudflare para evitar ese bloqueo.

La consola va a imprimir la URL exacta al arrancar. Con `--tunnel`, se ve algo así:

```
- API: https://<algo-random>.trycloudflare.com
- Studio UI: https://smith.langchain.com/studio/?baseUrl=https://<algo-random>.trycloudflare.com
```

> **Primera vez con cada túnel nuevo**: LangGraph Studio va a mostrar
> `Failed to connect to Agent Server because the domain "..." is not
> allowed`. Hacé clic en **"Configure connection"** (o Advanced Settings) en
> esa misma pantalla y agregá el dominio `*.trycloudflare.com` mostrado a la
> lista de dominios permitidos. El dominio cambia en cada reinicio del
> túnel, así que este paso se repite cada vez que se reinicia
> `langgraph dev --tunnel`.

Ahí, en el panel de invocación, se ingresa un JSON como este:

```json
{
  "pregunta": "cuantos usuarios hay?",
  "errores": [],
  "intentos_ejecucion": 0,
  "intentos_generacion_sql": 0
}
```

**Opción B — API REST:**

```powershell
python src/agent/api.py
```

Queda disponible en:
- `http://localhost:8000/chat` — endpoint principal (`POST`)
- `http://localhost:8000/docs` — documentación interactiva (Swagger UI), generada automáticamente por FastAPI, para probar el endpoint desde el navegador sin necesidad de comandos

Ejemplo de invocación por comando:

```powershell
Invoke-RestMethod -Uri http://localhost:8000/chat -Method Post -ContentType "application/json" -Body '{"texto": "cuantos usuarios hay?"}'
```

## Seguridad

Antes de comittear, corré el escáner de secretos incluido en el repo para
detectar credenciales hardcodeadas (connection strings con password en texto
plano, API keys de Groq/Gemini, tokens de GitLab):

```powershell
python scripts/check_secrets.py
```

Sale con código `0` si no encuentra nada, o con código `1` e imprime
`archivo:línea` de cada hallazgo si detecta algo sospechoso.

## Variables de entorno

| Variable         | Requerida | Descripción                                                        | De dónde sale                                              |
|------------------|-----------|---------------------------------------------------------------------|-------------------------------------------------------------|
| `DATABASE_URL`   | Sí        | Connection string completa de Postgres, incluye el nombre de la DB | La armás vos según el usuario/password del contenedor `postgres-local` |
| `APP_DB_TABLE`   | No        | Nombre de la tabla de demo que siembra el bootstrap (default: `Usuario`) | — |
| `APP_DB_SCHEMA`  | No        | Esquema a introspeccionar (default: `public`)                     | — |
| `NEO4J_URI`      | No        | URI de Neo4j (default: `neo4j://localhost:7687`)                  | — |
| `NEO4J_USER`     | No        | Usuario de Neo4j (default: `neo4j`)                                | — |
| `NEO4J_PASSWORD` | Sí        | Password de Neo4j                                                  | La que elegiste en `NEO4J_AUTH=neo4j/tu_password` al correr `docker run` del contenedor `neo4j-local` |
| `GEMINI_API_KEY` | Sí        | API key de Google AI Studio (Gemini)                               | [Google AI Studio](https://aistudio.google.com/apikey) — es personal, no se comparte ni se commitea |

> Nota técnica: el proyecto usa el alias de modelo `gemini-flash-latest` (no
> `gemini-2.5-flash`), porque ese modelo específico dejó de estar disponible
> para API keys nuevas (`404 no longer available to new users`) pese a figurar
> en el listado de `/models` de la API. El alias `-latest` apunta siempre al
> Flash vigente, evitando este problema a futuro.

> ⚠️ **Límite de cuota gratuita de Gemini**: el free tier permite solo **5
> requests por minuto** y un límite diario por modelo. Cada pregunta que
> procesa el agente hace hasta **3 llamadas a Gemini** en cadena (análisis
> de seguridad → generación de SQL → generación de respuesta), así que con
> 2 preguntas seguidas se puede agotar el límite por minuto. Cuando esto
> pasa, el agente responde con un mensaje honesto de servicio no disponible
> temporalmente (HTTP 503 en `/chat`), distinto del mensaje de rechazo por
> seguridad — ya no se confunden entre sí.

## Roadmap

**Fase 1 (✅ completada y validada)**: entorno local funcional en Windows, sin
credenciales hardcodeadas, esquema de DB agnóstico por introspección
dinámica, Gemini integrado vía LangChain, bugs conocidos de arranque
corregidos. Verificado end-to-end tanto en LangGraph Studio como en modo API
(`/chat`): pregunta en lenguaje natural → SQL generado → ejecutado contra
Postgres → respuesta en lenguaje natural. `graph.py` se mantiene como un
único archivo (sin modularizar).

**Fase 2 (✅ completada, 6/6)**: propuestas de mejora, gestionadas con SDD (ver
sección "Desarrollo y documentación de cambios" más abajo):
- `fix-security-credentials`
- `cleanup-docs-deps`
- `add-test-coverage`
- `refactor-graph-architecture` (modularizó `graph.py` en archivos separados)
- `fix-conftest-mock-leak`
- `distinguish-llm-transient-errors`

## Roadmap — Nuevas funcionalidades

Funcionalidad nueva, no cubierta por la Fase 2 (que fue refactor/limpieza de lo
existente):
- `add-conversation-memory`

## Desarrollo y documentación de cambios

Este proyecto combina dos herramientas para mantener contexto entre sesiones
de desarrollo (incluido con asistentes de IA):

- **Engram (Gentle AI)**: memoria persistente que sobrevive entre sesiones —
  decisiones, bugs encontrados, descubrimientos técnicos, y contexto de por
  qué se hizo algo. No vive en este repo (es una base de datos externa al
  proyecto), se consulta a través del asistente de IA que se use para
  trabajar en el código.
- **OpenSpec** (`openspec/`): specs formales versionadas en git para cambios y
  funcionalidades principales — cada cambio bajo `openspec/changes/{nombre}/`
  tiene su `proposal.md` (qué y por qué), `specs/` (requisitos formales),
  `design.md` (decisiones técnicas) y `tasks.md` (desglose de implementación
  con checklist). Los cambios completados se archivan en
  `openspec/changes/archive/`.

Para retomar el desarrollo de una funcionalidad o cambio en curso, revisá la
carpeta correspondiente en `openspec/changes/`. Para entender decisiones
puntuales o bugs ya resueltos que no ameritan una spec formal, esos quedan en
Engram (accesible vía el asistente de IA).
