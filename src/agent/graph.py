import asyncio
import threading
import json
import os

# Debe fijarse ANTES de importar torch/sentence-transformers (más abajo):
# evita un deadlock real en Windows cuando el modelo de embeddings corre
# inferencia desde un thread distinto al que lo cargó (típico al usar
# asyncio.to_thread para no bloquear el event loop de FastAPI/uvicorn).
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import re
import sys
from datetime import datetime
from pathlib import Path

from google.api_core.exceptions import (
    ResourceExhausted,
    DeadlineExceeded,
    ServiceUnavailable,
    InternalServerError,
    GatewayTimeout,
    Aborted,
    RetryError,
    TooManyRequests,
)
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

# Exceptions considered transient provider errors (rate-limit, timeout,
# service/network failure), never a real security rejection. See design.md
# for the rationale behind each class, including TooManyRequests as a
# version-skew safeguard across google-api-core releases.
TRANSIENT_LLM_EXCEPTIONS = (
    ResourceExhausted,
    DeadlineExceeded,
    ServiceUnavailable,
    InternalServerError,
    GatewayTimeout,
    Aborted,
    RetryError,
    TooManyRequests,
    ConnectionError,
    TimeoutError,
    OSError,
)

try:
    # Import relativo normal: funciona cuando el módulo se carga como parte
    # del paquete "agent" (pytest, api.py, `import agent.graph`).
    from .state import State
    from .neo4j_manager import Neo4jGraph, Neo4jManager
    from .sql_processing import SQLProcessor, obtener_ddl_dinamico
    from .rag import SQLRAGSystem
    from .config import ChatbotConfig
except ImportError:
    # Fallback sin paquete: `langgraph dev` carga graph.py por RUTA DE
    # ARCHIVO directa (importlib.util.spec_from_file_location), no como
    # parte del paquete "agent" — ahí un import relativo falla con
    # "attempted relative import with no known parent package". En ese
    # caso, el propio directorio de graph.py ya está en sys.path (o se
    # agrega acá), así que los módulos hermanos son importables sin punto.
    sys.path.insert(0, str(Path(__file__).parent))
    from state import State
    from neo4j_manager import Neo4jGraph, Neo4jManager
    from sql_processing import SQLProcessor, obtener_ddl_dinamico
    from rag import SQLRAGSystem
    from config import ChatbotConfig

API_KEY_GEMINI = os.getenv("GEMINI_API_KEY", "")


def route_after_security_analysis(state: State) -> str:
    """Conditional-edge routing function for the security-analysis node.

    Routes off the structured `decision_seguridad.tipo` field set by
    `LangGraphAgent.analizar_seguridad` (present only when the question was
    rejected, either "rechazo_seguridad" or "error_transitorio") instead of
    string-sniffing "seguridad" inside free-text error messages. The old
    string match silently missed the dangerous-keyword regex rejection path,
    whose error message ("Consulta contiene operaciones peligrosas: ...")
    never contains the word "seguridad" - a real safety-invariant bypass
    letting flagged questions reach `generar_sql`. Extracted to module level
    (out of the `_build_graph` closure) so it can be unit-tested directly
    against the actual routing decision, not just the state fields it reads.
    """
    if state.get("decision_seguridad", {}).get("tipo"):
        print("Consulta rechazada, yendo a rechazar_pregunta")
        return "rechazar_pregunta"
    pregunta = state.get("pregunta", "").lower()
    sql_keywords = ["select", "lista", "cuántos", "cuantas", "cuántas", "mostrar", "buscar", "encontrar", "consultar"]
    if any(keyword in pregunta for keyword in sql_keywords):
        print("Consulta parece requerir SQL, procediendo a buscar contexto")
        return "buscar_contexto"
    print("Consulta no parece requerir SQL, procediendo a buscar contexto")
    return "buscar_contexto"


class LangGraphAgent:
    # Ejemplos few-shot para el prompt de generación SQL. Vacío por defecto:
    # el proyecto es agnóstico de dominio (cualquier DB Postgres), no hay
    # ejemplos válidos de forma genérica. Se puede poblar por config/env
    # si se quiere dar few-shot examples específicos de una DB concreta.
    EJEMPLOS_CONSULTAS = []

    def __init__(self):
        self.embedding_model = None
        self.llm = None
        self.graph_instance = None
        self.config = ChatbotConfig()
        print("LangGraphAgent inicializado")

    async def _init_models(self):
        try:
            print("LangGraphAgent _init_models Inicializando configuración completa.")
            await self.config.initialize()

            print(" LangGraphAgent _init_models Configurando modelo LLM para LangGraph.")
            # No inicializar LLM aquí, usaremos Gemini directamente
            self.llm = None  # Mantener como None ya que usaremos Gemini directamente
            print(" LangGraphAgent _init_models Usando Gemini API directamente")

            self._build_graph()
            print("LangGraphAgent _init_models Grafo construido exitosamente")

        except Exception as e:
            print(f"Error en inicialización de modelos: {str(e)}")
            raise

    def _build_graph(self):
        global graph
        print("LangGraphAgent _build_graph Iniciando construcción del grafo")
        graph_builder = StateGraph(State)

        async def analizar_seguridad_node(state: State):
            return await self.analizar_seguridad(state)

        async def buscar_contexto_node(state: State):
            return await self.buscar_contexto(state)

        async def generar_respuesta_node(state: State):
            return await self.generar_respuesta(state)

        async def rechazar_pregunta_node(state: State):
            return await self.rechazar_pregunta(state)

        async def manejar_respuesta_llm_node(state: State):
            return await self.manejar_respuesta_llm(state)

        async def generar_sql_node(state: State):
            return await self.generar_sql(state)

        async def validar_sql_node(state: State):
            return await self.validar_sql(state)

        async def ejecutar_sql_node(state: State):
            return await self.ejecutar_sql(state)

        def after_security_analysis(state: State):
            return route_after_security_analysis(state)

        def after_validation(state: State):
            sql_valido = state.get("sql_valido", False)
            print(f"--- after_validation ---")
            print(f"SQL válido: {sql_valido}")
            print("Procediendo a ejecución de SQL (independientemente de su validez)")
            return "ejecutar_sql"

        def after_execution(state: State):
            intentos_ejecucion = state.get("intentos_ejecucion", 0)
            ejecucion_exitosa = state.get("ejecucion_exitosa", False)

            print(f"=== AFTER_EXECUTION ===")
            print(f"Valor de state['intentos_ejecucion']: {state.get('intentos_ejecucion', 'NO DEFINIDO')}")
            print(f"Intentos de ejecucion: {intentos_ejecucion}")
            print(f"Ejecucion exitosa: {ejecucion_exitosa}")

            if ejecucion_exitosa:
                print("Decision: SQL ejecutado exitosamente -> generar_respuesta")
                return "generar_respuesta"

            elif intentos_ejecucion < 3:
                print(f"Decision: Fallo ejecucion SQL (intento {intentos_ejecucion + 1}/3) -> generar_sql")
                return "generar_sql"

            else:
                print("Decision: Fallo despues de 3 intentos -> generar_respuesta")
                return "generar_respuesta"

        graph_builder.add_node("analizar_seguridad", analizar_seguridad_node)
        graph_builder.add_node("buscar_contexto", buscar_contexto_node)
        graph_builder.add_node("generar_respuesta", generar_respuesta_node)
        graph_builder.add_node("rechazar_pregunta", rechazar_pregunta_node)
        graph_builder.add_node("manejar_respuesta_llm", manejar_respuesta_llm_node)
        graph_builder.add_node("generar_sql", generar_sql_node)
        graph_builder.add_node("validar_sql", validar_sql_node)
        graph_builder.add_node("ejecutar_sql", ejecutar_sql_node)
        print("LangGraphAgent_build_graph Todos los nodos agregados")

        graph_builder.add_edge(START, "analizar_seguridad")
        graph_builder.add_conditional_edges(
            "analizar_seguridad",
            after_security_analysis,
            {
                "rechazar_pregunta": "rechazar_pregunta",
                "buscar_contexto": "buscar_contexto"
            }
        )
        graph_builder.add_edge("rechazar_pregunta", "manejar_respuesta_llm")
        graph_builder.add_edge("buscar_contexto", "generar_sql")
        graph_builder.add_edge("generar_sql", "validar_sql")
        graph_builder.add_conditional_edges(
            "validar_sql",
            after_validation,
            {
                "ejecutar_sql": "ejecutar_sql",
            }
        )
        graph_builder.add_conditional_edges(
            "ejecutar_sql",
            after_execution,
            {
                "generar_respuesta": "generar_respuesta",
                "generar_sql": "generar_sql"
            }
        )
        graph_builder.add_edge("generar_respuesta", "manejar_respuesta_llm")
        self.graph_instance = graph_builder.compile()
        graph = self.graph_instance
        print("Grafo de LangGraph")

    async def manejar_respuesta_llm(self, state: State) -> State:
        try:
            if state.get("respuesta_natural"):
                print("1. Usando respuesta_natural existente del estado")
                respuesta_final = state["respuesta_natural"]
                print(f"2. Respuesta final obtenida: {respuesta_final[:100]}")
            else:
                print("3. Generando nueva respuesta con LLM")
                print(f"4. Pregunta: {state.get('pregunta', 'No disponible')}")
                print(f"5. Contexto: {state.get('contexto', 'Sin contexto')[:100]}")

                # Usar Gemini para generar la respuesta
                print("6. Invocando Gemini API para generar respuesta")
                respuesta_gemini = await self.config.chat_gemini(
                    state["pregunta"],
                    contexto_rag=state.get("contexto", "")
                )
                
                respuesta_final = respuesta_gemini.strip()
                print(f"7. Respuesta Gemini generada: {len(respuesta_final)} caracteres")
                print(f"8. Preview respuesta: {respuesta_final[:100]}")

            mensajes_actuales = state.get("messages", [])
            print(f"9. Mensajes actuales antes: {len(mensajes_actuales)}")

            mensajes_actuales.append(AIMessage(content=respuesta_final))
            print(f"10. Mensajes actuales después: {len(mensajes_actuales)}")

            estado_retorno = {
                "messages": mensajes_actuales,
                "pregunta": "",
                "errores": [],
                "contexto": state.get("contexto", ""),
                "sql_generado": None,
                "ejecucion_exitosa": True,
                "intentos_ejecucion": 0,
                "intentos_generacion_sql": 0,
                "respuesta_natural": respuesta_final
            }

            print("11. Estado preparado para retorno")
            print("12. Limpiando pregunta y errores para siguiente iteración")
            print("=== FINALIZANDO manejar_respuesta_llm (éxito) ===")

            return estado_retorno

        except Exception as e:
            print(f"ERROR en manejar_respuesta_llm: {str(e)}")
            error_msg = f"Error manejando respuesta LLM: {str(e)}"

            mensajes_actuales = state.get("messages", [])
            print(f"13. Error - mensajes actuales: {len(mensajes_actuales)}")

            mensaje_error = "Lo siento, ocurrió un error procesando tu solicitud."
            mensajes_actuales.append(AIMessage(content=mensaje_error))
            print("14. Mensaje de error agregado al chat")

            estado_error = {
                "messages": mensajes_actuales,
                "pregunta": "",
                "errores": [error_msg],
                "contexto": state.get("contexto", ""),
                "sql_generado": None,
                "ejecucion_exitosa": False,
                "intentos_ejecucion": 0,
                "intentos_generacion_sql": 0,
                "respuesta_natural": mensaje_error
            }

            print("15. Retornando estado con error")
            print("=== FINALIZANDO manejar_respuesta_llm (con error) ===")

            return estado_error


    

    async def stream_response(self, user_input: str, thread_id: str = "1"):
        if not self.graph_instance:
            print("Inicializando modelos on-demand")
            await self._init_models()
        
        print(f"Procesando input del usuario: '{user_input}' en thread: {thread_id}")
        
        
        config = {"configurable": {"thread_id": thread_id}}
        
        initial_state = {
            "messages": [HumanMessage(content=user_input)],
            "pregunta": user_input,  
            "errores": [],
            "contexto": "",
            "sql_generado": None,
            "sql_ast": None,
            "resultados_sql": None,
            "resultados_neo4j": None,
            "respuesta_natural": None,
            "ejecucion_exitosa": False,
            "intentos_ejecucion": 0,
            "intentos_generacion_sql": 0,
            "decision_seguridad": None,
            "metadata": {},
            "ddl": self.config.ddl,  
            "sql_valido": False,
            "metricas_busqueda": None
        }
        
        full_response = ""
        try:
            async for event in self.graph_instance.astream(
                initial_state,
                config,
                stream_mode="values"
            ):
                
                if "messages" in event and event["messages"]:
                    last_message = event["messages"][-1]
                    if isinstance(last_message, AIMessage) and hasattr(last_message, 'content') and last_message.content:
                        print("Asistente:", last_message.content)
                        full_response += last_message.content
                    elif hasattr(last_message, 'tool_calls') and last_message.tool_calls:
                        tool_name = last_message.tool_calls[0]['name']
                        print(f"Asistente: [Llamando herramienta: {tool_name}]")
                
                
                if "metricas_busqueda" in event:
                    print(f"Métricas de búsqueda: {event['metricas_busqueda']}")
                    
            
            print(f"Respuesta completada. Longitud: {len(full_response)} caracteres")
            
        except Exception as e:
            print(f"Error durante el streaming de respuesta: {str(e)}")
            raise
        
        return full_response


    async def analizar_seguridad(self, state: State):
        # PRINT INICIAL - Estado al comienzo
        print("=" * 60)
        print("ANALIZAR_SEGURIDAD - ESTADO INICIAL:")
        print("=" * 60)
        print(f"Keys en state: {list(state.keys())}")
        for key, value in state.items():
            if key == "messages" and isinstance(value, list):
                print(f"  {key}: {len(value)} mensajes")
                for i, msg in enumerate(value):
                    msg_type = type(msg).__name__
                    content_preview = getattr(msg, 'content', '')[:100] + '...' if getattr(msg, 'content', '') else 'sin contenido'
                    print(f"    [{i}] {msg_type}: {content_preview}")
            elif key == "errores" and isinstance(value, list):
                print(f"  {key}: {len(value)} errores - {value}")
            elif key == "embedding" and isinstance(value, list):
                print(f"  {key}: lista de {len(value)} dimensiones")
            elif isinstance(value, (str, int, float, bool)) or value is None:
                value_preview = str(value)[:100] + '...' if value and len(str(value)) > 100 else str(value)
                print(f"  {key}: {value_preview}")
            else:
                print(f"  {key}: {type(value)}")
        print("=" * 60)
        
        print("Iniciando análisis")
        if "errores" not in state:
            print("Añadiendo lista de errores al estado")
            state["errores"] = []
        try:
            pregunta = state.get("pregunta", "")
            if not pregunta and state.get("messages"):
                for msg in reversed(state["messages"]):
                    if isinstance(msg, HumanMessage) and hasattr(msg, 'content'):
                        pregunta = msg.content
                        state["pregunta"] = pregunta
                        break
            print(f"Pregunta obtenida: '{pregunta}'")
            if not pregunta or pregunta.strip() == "":
                error_msg = "No se proporcionó una pregunta para analizar"
                print(f"Error: {error_msg}")
                state["errores"].append(error_msg)
                return state
            print(f"Pregunta obtenida: '{pregunta}'")

            dangerous_keywords = [
                "insertar", "actualizar", "eliminar", "eliminartabla", "truncar", "modificar", "crear",
                "modificar", "remover", "borrar", "limpiar", "destruir", "conceder", "revocar",
                "ejecutar", "confirmar", "revertir", "transacción", "procedimiento", "función",
                "disparador", "vista", "índice", "restricción", "contraseña", "secreto", "tarjeta de crédito",
                "contraseña", "tarjeta", "credenciales"
            ]
            print(f"Lista de palabras peligrosas cargada: {len(dangerous_keywords)} elementos")
            pregunta_lower = pregunta.lower()
            print(f"Pregunta en minúsculas: '{pregunta_lower}'")
            palabras_peligrosas_encontradas = [
                palabra for palabra in dangerous_keywords
                if palabra in pregunta_lower
            ]
            print(f"Palabras peligrosas encontradas: {palabras_peligrosas_encontradas}")

            if palabras_peligrosas_encontradas:
                razon = f"contiene operaciones peligrosas: {', '.join(palabras_peligrosas_encontradas[:3])}"
                print(f"Palabras peligrosas detectadas, razón: {razon}")
                state["decision_seguridad"] = {
                    "es_segura": False,
                    "razon": razon,
                    "riesgo": "alto",
                    "tipo": "rechazo_seguridad",
                    "palabras_peligrosas": palabras_peligrosas_encontradas
                }
                error_msg = f"Consulta contiene operaciones peligrosas: {palabras_peligrosas_encontradas}"
                state["errores"].append(error_msg)
                return state

            print("Creando prompt para análisis con LLM")
            prompt_text = """Eres un analizador de seguridad para consultas de base de datos.
            Analiza la petición del usuario y responde ÚNICAMENTE con un objeto JSON válido.

            EL FORMATO DE RESPUESTA DEBE SER EXACTAMENTE:

            {
            "es_segura": true/false,
            "razon": "texto explicativo",
            "riesgo": "bajo/medio/alto",
            "intencion": "consulta/modificacion/eliminacion/etc"
            }

            NO INCLUYAS NINGÚN TEXTO ADICIONAL, EXPLICACIONES, COMENTARIOS O FORMATACIÓN.
            NO USES MARKDOWN, NO USES ```json ```, NI NINGÚN OTRO ENVOLTORIO.
            SOLO EL OBJETO JSON.

            Considera peligrosas las consultas que:
            - INSERT, UPDATE, DELETE, DROP, TRUNCATE, ALTER, CREATE
            - Acceden a datos sensibles como contraseñas, información personal
            - Podrían causar pérdida de datos
            - Involucran inyección SQL o técnicas maliciosas

            Pregunta del usuario: """ + pregunta

            print("Invocando Gemini para análisis de seguridad...")
            try:
                # Se envía como mensaje directo (sin ChatPromptTemplate) para evitar que
                # las llaves literales del ejemplo JSON sean interpretadas como variables
                # de template por LangChain.
                def _invocar_sync():
                    # Construcción + invocación juntas en el mismo thread: si la
                    # construcción del cliente Gemini también hace I/O síncrona,
                    # envolver solo el .invoke() no alcanza para escapar del
                    # detector de bloqueo de "langgraph dev".
                    llm = ChatGoogleGenerativeAI(model="gemini-flash-latest", google_api_key=API_KEY_GEMINI)
                    return llm.invoke([HumanMessage(content=prompt_text)])

                resultado = await asyncio.to_thread(_invocar_sync)
                respuesta_texto = resultado.content.strip()
                respuesta_texto = re.sub(r"```json|```", "", respuesta_texto).strip()
                try:
                    decision = json.loads(respuesta_texto)
                    print(f"Respuesta de Gemini recibida: {decision}")
                    state["decision_seguridad"] = decision

                    if not decision.get("es_segura", False):
                        decision["tipo"] = "rechazo_seguridad"
                        razon = decision.get("razon", f"Riesgo {decision.get('riesgo', 'alto')}")
                        error_msg = f"Consulta rechazada por seguridad: {razon}"
                        print(f"Consulta no segura: {error_msg}")
                        state["errores"].append(error_msg)
                    else:
                        print("Consulta considerada segura")

                    print("Análisis de seguridad completado exitosamente")
                except json.JSONDecodeError:
                    print("Error: Gemini no devolvió JSON válido")
                    state["decision_seguridad"] = {
                        "es_segura": False,
                        "razon": "error en el análisis de seguridad",
                        "riesgo": "alto",
                        "tipo": "error_transitorio"
                    }
                    state["errores"].append("Error en análisis de seguridad: respuesta no válida")
            except TRANSIENT_LLM_EXCEPTIONS as e:
                print(f"Error transitorio en llamada a Gemini: {str(e)}")
                state["decision_seguridad"] = {
                    "es_segura": False,
                    "razon": "error en el análisis de seguridad",
                    "riesgo": "alto",
                    "tipo": "error_transitorio"
                }
                state["errores"].append(f"Error en análisis de seguridad: {str(e)}")
            except Exception as e:
                print(f"Error en llamada a Gemini: {str(e)}")
                state["decision_seguridad"] = {
                    "es_segura": False,
                    "razon": "error en el análisis de seguridad",
                    "riesgo": "alto",
                    "tipo": "error_transitorio"
                }
                state["errores"].append(f"Error en análisis de seguridad: {str(e)}")

        except Exception as e:
            error_msg = f"Error en análisis de seguridad: {str(e)}"
            print(f"EXCEPCIÓN CAPTURADA: {error_msg}")
            state["errores"].append(error_msg)
            state["decision_seguridad"] = {
                "es_segura": False,
                "razon": "error en el análisis de seguridad",
                "riesgo": "alto",
                "tipo": "error_transitorio"
            }
            print("Estado actualizado con información de error")
        
        print("=" * 60)
        print("ANALIZAR_SEGURIDAD - ESTADO FINAL:")
        print("=" * 60)
        print(f"Keys en state: {list(state.keys())}")
        for key, value in state.items():
            if key == "messages" and isinstance(value, list):
                print(f"  {key}: {len(value)} mensajes")
            elif key == "errores" and isinstance(value, list):
                print(f"  {key}: {len(value)} errores - {value}")
            elif key == "decision_seguridad" and isinstance(value, dict):
                print(f"  {key}:")
                for k, v in value.items():
                    print(f"    {k}: {v}")
            elif key == "embedding" and isinstance(value, list):
                print(f"  {key}: lista de {len(value)} dimensiones")
            elif isinstance(value, (str, int, float, bool)) or value is None:
                value_preview = str(value)[:100] + '...' if value and len(str(value)) > 100 else str(value)
                print(f"  {key}: {value_preview}")
            else:
                print(f"  {key}: {type(value)}")
        print("=" * 60)
        
        print("Retornando estado final")
        return state

    async def buscar_contexto(self, state: State) -> State:
        print(f"Buscando contexto RAG para pregunta: {state.get('pregunta', '')}")
        
        try:
            state["ddl"] = await self.config._obtener_ddl()
            def obtener_rag_completo(pregunta: str):
                try:
                    rag_system = SQLRAGSystem()
                    return rag_system.obtener_contexto_rag(pregunta)
                except Exception as e:
                    print(f"Error en obtener_rag_completo: {str(e)}")
                    return ""
            
            print("Obteniendo contexto RAG de PostgreSQL...")
            
            contexto_rag = await asyncio.to_thread(
                obtener_rag_completo,
                state["pregunta"]
            )
            
            print(f"Contexto RAG obtenido: {len(contexto_rag) if contexto_rag else 0} caracteres")
            
            # Variables temporales para mantener compatibilidad
            similares = None
            neo4j_count = 0
            
            ejemplos_consultas = "\n\n--- EJEMPLOS DE CONSULTAS ---\n" + "\n\n".join(
                f"Input: {ej['input']}\nSQL: {ej['query'][:200]}..."
                if len(ej['query']) > 200 else f"Input: {ej['input']}\nSQL: {ej['query']}"
                for ej in self.EJEMPLOS_CONSULTAS[:2]
            )
            
            ddl_content = state["ddl"]
            if len(ddl_content) > 5000:
                ddl_content = ddl_content[:5000] + "\n... (estructura truncada por tamaño)"
            
            partes_contexto = [
                "--- ESTRUCTURA BASE DE DATOS ---",
                ddl_content
            ]
            
            if contexto_rag and len(contexto_rag.strip()) > 50:
                partes_contexto.extend([
                    "\n\n--- EJEMPLOS SIMILARES (RAG PostgreSQL) ---",
                    contexto_rag
                ])
            
            # Sección de Neo4j
            # if similares and neo4j_count > 0:
            #     partes_contexto.extend([
            #         "\n\n--- CONSULTAS SIMILARES HISTÓRICAS (Neo4j) ---",
            #         "\n".join(
            #             f"- {q['question']} (SQL: {q['sql'][:100]}...)" 
            #             if len(q['sql']) > 100 else f"- {q['question']} (SQL: {q['sql']})"
            #             for q in similares[:2]
            #         )
            #     ])
            
            partes_contexto.append("\n\n" + ejemplos_consultas)
            
            state["contexto"] = "\n".join(partes_contexto)
            
            print(f"\n{'='*60}")
            print("PREVIEW DEL CONTEXTO GENERADO:")
            print(f"{'='*60}")
            print(f"Longitud total: {len(state['contexto'])} caracteres")
            print(f"{'='*60}")
            
            lineas_contexto = state["contexto"].split('\n')
            lineas_a_mostrar = len(lineas_contexto) 
            
            print(f"Primeras {lineas_a_mostrar} líneas:")
            print(f"{'-'*40}")
            for i in range(lineas_a_mostrar):
                if i < len(lineas_contexto):
                    linea = lineas_contexto[i]
                    if len(linea) > 150:
                        linea = linea[:150] + "..."
                    print(f"{i+1:2d}: {linea}")
            
            print(f"{'-'*40}")
            
            print(f"\nESTADÍSTICAS DEL CONTEXTO:")
            print(f"{'-'*40}")
            print(f"• Líneas totales: {len(lineas_contexto)}")
            print(f"• Tiene contexto RAG: {'SÍ' if contexto_rag and len(contexto_rag.strip()) > 50 else 'NO'}")
            
            if contexto_rag:
                lineas_rag = contexto_rag.split('\n')
                print(f"• Líneas de RAG: {len(lineas_rag)}")
                ejemplos_sql_rag = sum(1 for linea in lineas_rag if "SQL:" in linea)
                print(f"• Ejemplos SQL en RAG: {ejemplos_sql_rag}")
            
            print(f"• Ejemplos predefinidos mostrados: {len(self.EJEMPLOS_CONSULTAS[:2])}")
            print(f"{'='*60}\n")
            
            state["metricas_busqueda"] = {
                "fuente": "postgresql_rag",
                "tiene_contexto_rag": bool(contexto_rag and len(contexto_rag.strip()) > 50),
                "longitud_rag": len(contexto_rag) if contexto_rag else 0,
                "consultas_neo4j": 0,  
                "neo4j_habilitado": False,  
                "longitud_total_contexto": len(state["contexto"]),
                "lineas_contexto": len(lineas_contexto),
                "ejemplos_sql_encontrados": ejemplos_sql_rag if contexto_rag else 0
            }
            
        except Exception as e:
            print(f"Error en buscar_contexto: {str(e)}")
            import traceback
            traceback.print_exc()
            
            state["errores"].append(f"Error buscando contexto: {str(e)}")
            
            # Contexto mínimo con solo DDL en caso de error
            ddl_fallback = state.get("ddl") or await self.config._obtener_ddl()
            state["contexto"] = f"--- ESTRUCTURA BASE DE DATOS ---\n{ddl_fallback[:3000]}"
            state["metricas_busqueda"] = {
                "fuente": "error",
                "tiene_contexto_rag": False,
                "neo4j_habilitado": False,
                "error": str(e)[:100]
            }
        
        return state

    async def generar_respuesta(self, state: State) -> State:
        print("=== generar_respuesta ===")

        try:
            resultados_sql = state.get("resultados_sql")
            print(f"1. resultados_sql obtenido: {type(resultados_sql)}")

            if resultados_sql is None:
                print("2. resultados_sql es None")
                resultados_str = "No se pudo realizar la consulta debido a errores"
            elif isinstance(resultados_sql, list):
                print(f"3. resultados_sql es lista con {len(resultados_sql)} elementos")
                if len(resultados_sql) == 0:
                    print("4. Lista vacía - sin resultados")
                    resultados_str = "La consulta se ejecutó correctamente pero no devolvió resultados"
                else:
                    total_registros = len(resultados_sql)
                    print(f"5. {total_registros} registros encontrados")
                    resultados_str = f"Se encontraron {total_registros} registros"

                    if total_registros > 10:
                        print(f"6. Limitando a 10 de {total_registros} registros")
                        resultados_str += f" (analizando solo los primeros 10):\n"
                        registros_a_analizar = resultados_sql[:10]
                    else:
                        print("7. Analizando todos los registros")
                        resultados_str += ":\n"
                        registros_a_analizar = resultados_sql

                    print(f"8. Procesando {len(registros_a_analizar)} registros")

                    if registros_a_analizar:
                        print(f"9. Primera fila keys: {list(registros_a_analizar[0].keys())[:5]}...")

                    print("10. Preparando detalles de registros...")
                    for i, fila in enumerate(registros_a_analizar[:3]):
                        resultados_str += f"\nFila {i+1}:\n"
                        for clave, valor in fila.items():
                            resultados_str += f"  {clave}: {valor}\n"

                    if len(registros_a_analizar) > 3:
                        print(f"11. Mostrando 3 de {len(registros_a_analizar)} registros")
                        resultados_str += f"\n... mostrando 3 de {len(registros_a_analizar)} registros analizados"

                    if total_registros > 10:
                        print(f"12. Hay {total_registros - 10} registros adicionales")
                        resultados_str += f"\n\nNOTA: Hay {total_registros - 10} registros adicionales no incluidos en este análisis"
            else:
                print(f"13. Tipo inesperado: {type(resultados_sql)}")
                resultados_str = str(resultados_sql)

            print(f"14. Longitud de resultados_str: {len(resultados_str)} caracteres")

            print("15. Creando prompt para Gemini.")
            prompt_text = f"""Eres un experto analista de datos que explica resultados. Considera:
                1. Habla en forma coloquial pero no demasiado
                2. Explica brevemente la respuesta basándote SOLO en los resultados proporcionados
                3. Si no hay resultados, indica que la consulta no devolvió datos
                4. Si hay resultados, presenta un resumen claro y conciso
                5. No uses markdown, solo texto plano
                6. NO inventes información que no esté en los resultados
                7. Si se indica que hay registros adicionales, menciona brevemente que existen más datos disponibles

                Resultados obtenidos:
                {resultados_str}

                Genera una respuesta profesional y clara basada exclusivamente en estos resultados:"""

            print("16. Invocando Gemini")
            try:
                def _invocar_sync():
                    # Construccion + invocacion juntas en el mismo thread: si la
                    # construccion del cliente Gemini tambien hace I/O sincrona,
                    # envolver solo el .invoke() no alcanza para escapar del
                    # detector de bloqueo de "langgraph dev".
                    llm = ChatGoogleGenerativeAI(model="gemini-flash-latest", google_api_key=API_KEY_GEMINI)
                    return llm.invoke([HumanMessage(content=prompt_text)])

                resultado = await asyncio.to_thread(_invocar_sync)
                respuesta_texto = resultado.content.strip()
                respuesta_texto = respuesta_texto.replace("```markdown", "").replace("```", "").strip()
                print(f"17. Respuesta generada: {len(respuesta_texto)} caracteres")
            except Exception as e:
                respuesta_texto = f"Error al generar respuesta: {str(e)}"
                print(f"17. Error en llamada a Gemini: {str(e)}")


            print("18. Preparando metadata")
            sql_ast = state.get("sql_ast", {})
            tablas = sql_ast.get("from", []) if isinstance(sql_ast, dict) else []
            columnas = sql_ast.get("select", []) if isinstance(sql_ast, dict) else []

            metadata = {
                "modelo_usado": "gemini-flash-latest",
                "intencion": sql_ast.get("intention", "consulta general") if isinstance(sql_ast, dict) else "consulta general",
                "tablas": tablas,
                "columnas": columnas,
                "exito": not state.get("errores", []),
                "num_resultados": len(resultados_sql) if isinstance(resultados_sql, list) else 0,
                "timestamp": datetime.now().isoformat()
            }

            print("19. Retornando estado final")
            return {
                **state,
                "respuesta_natural": respuesta_texto,
                "metadata": {**state.get("metadata", {}), **metadata}
            }

        except Exception as e:
            print(f"ERROR en generar_respuesta: {str(e)}")
            error_msg = f"Error generando respuesta: {str(e)}"
            return {
                **state,
                "respuesta_natural": "No pude generar una respuesta adecuada. Por favor revisa los resultados manualmente.",
                "errores": state.get("errores", []) + [error_msg]
            }

    async def generar_sql(self, state: State) -> State:
        print("\n=== INICIANDO generar_sql ===")

        # Inicializar errores
        state.setdefault("errores", [])
        print(f"Errores actuales: {len(state['errores'])}")

        pregunta = state.get("pregunta", "")
        if not pregunta and state.get("messages"):
            print("Buscando pregunta en mensajes...")
            for msg in reversed(state["messages"]):
                if isinstance(msg, HumanMessage) and hasattr(msg, 'content'):
                    pregunta = msg.content
                    state["pregunta"] = pregunta
                    break

        if not pregunta:
            return self._finalizar_sql_con_error(
                state, "No se encontró una pregunta válida para generar SQL", "sin pregunta"
            )

        print(f"Pregunta a procesar: '{pregunta[:80]}...'")
        
        ddl_content = state.get("ddl") or await self.config._obtener_ddl()
        intentos_ejecucion = state.get("intentos_ejecucion", 0)
        error_previo = state.get("error_ejecucion_sql")

        feedback_correccion = ""
        if intentos_ejecucion > 0 and error_previo:
            feedback_correccion = f"""
            ERROR EN EJECUCIÓN PREVIA (Intento {intentos_ejecucion}):

            INSTRUCCIONES PARA CORREGIR:
            1. Analiza este error específico
            2. Revisa nombres de tablas/columnas
            3. Verifica tipos de datos en condiciones WHERE
            4. Asegura que la sintaxis SQL sea válida
            5. Considera simplificar la consulta si es necesario
            """

        consultas_similares = "\n\n--- EJEMPLOS DE CONSULTAS ---\n" + "\n\n".join(
            f"Input: {ej['input']}\nSQL: {ej['query'][:200]}..."
            if len(ej['query']) > 200 else f"Input: {ej['input']}\nSQL: {ej['query']}"
            for ej in self.EJEMPLOS_CONSULTAS[:3]
        )

        print("Creando prompt para generación de SQL...")
        prompt_text = f"""# Eres un experto en generar consultas SQL válidas para PostgreSQL
            ## ESQUEMA DE BASE DE DATOS:
            {ddl_content}
            ## DOCUMENTACIÓN ADICIONAL:
            {consultas_similares}
            {feedback_correccion}
            ## REGLAS:
            - SOLO consultas SELECT
            - SIN comentarios ni punto y coma
            - Usa solo las tablas y columnas del esquema

            Pregunta del usuario: {pregunta}
            Genera una consulta SQL válida:"""

        try:
            print("Invocando Gemini para generar SQL...")
            def _invocar_sync():
                # Construccion + invocacion juntas en el mismo thread: si la
                # construccion del cliente Gemini tambien hace I/O sincrona,
                # envolver solo el .invoke() no alcanza para escapar del
                # detector de bloqueo de "langgraph dev".
                llm = ChatGoogleGenerativeAI(model="gemini-flash-latest", google_api_key=API_KEY_GEMINI)
                return llm.invoke([HumanMessage(content=prompt_text)])

            resultado = await asyncio.to_thread(_invocar_sync)
            sql = resultado.content.strip()
            sql = re.sub(r"```sql|```", "", sql).strip()

            if not sql or "no pudo generar" in sql.lower():
                return self._finalizar_sql_con_error(
                    state, "Gemini no pudo generar una consulta SQL válida", "sin SQL"
                )

            print(f"SQL generado exitosamente por Gemini:\n{sql}")
            state["sql_generado"] = sql
            state["sql_ast"] = None
            state["ultimo_error"] = None
            print("=== FINALIZANDO generar_sql (éxito) ===")
            return state

        except Exception as e:
            error_msg = f"Error generando SQL con Gemini: {type(e).__name__}: {str(e)}"
            import traceback
            print(f"EXCEPCIÓN: {error_msg}")
            print(traceback.format_exc())

            state["errores"].append(error_msg)
            state["sql_generado"] = None
            state["sql_ast"] = None
            state["ultimo_error"] = error_msg
            print("=== FINALIZANDO generar_sql (con excepción) ===")
            return state

    def _finalizar_sql_con_error(self, state: State, error_msg: str, motivo: str) -> State:
        print(f"ERROR: {error_msg}")
        state["errores"].append(error_msg)
        state["sql_generado"] = None
        state["sql_ast"] = None
        state["ultimo_error"] = error_msg
        print(f"=== FINALIZANDO generar_sql ({motivo}) ===")
        return state


    async def ejecutar_sql(self, state: State) -> State:
        print("=== INICIANDO EJECUCIÓN SQL ===")
        sql_generado = state.get("sql_generado")
        sql_valido = state.get("sql_valido", False)
        intentos_ejecucion = state.get("intentos_ejecucion", 0)
        print(f"SQL generado: {sql_generado}")
        print(f"SQL válido: {sql_valido}")
        print(f"Intentos de ejecución previos: {intentos_ejecucion}")
        state["intentos_ejecucion"] = intentos_ejecucion + 1
        print(f"Nuevo contador de intentos: {state['intentos_ejecucion']}")
        if not sql_generado:
            error_msg = "No hay SQL generado para ejecutar"
            print(f" ERROR: {error_msg}")
            state["errores"] = state.get("errores", []) + [error_msg]
            state["ejecucion_exitosa"] = False
            print("=== EJECUCIÓN SQL FALLIDA ===")
            return state
        try:
            print("Ejecutando SQL")
            print(f" SQL a ejecutar: {sql_generado[:200]}")

            resultado_tupla = await asyncio.to_thread(
                self.config.sql_processor.execute_sql,
                sql_generado
            )
            print(f"Tipo de resultado completo: {type(resultado_tupla)}")
            resultados, error_ejecucion = resultado_tupla
            print(f"Resultados extraídos: {type(resultados)}")
            print(f"Error extraído: {error_ejecucion}")
            if error_ejecucion:
                print(f"Error en ejecución SQL: {error_ejecucion}")
                state["errores"] = state.get("errores", []) + [error_ejecucion]
                state["ejecucion_exitosa"] = False
                print("=== EJECUCIÓN SQL FALLIDA ===")
                return state
            print("SQL ejecutado exitosamente")

            if resultados is None:
                print("Resultados: None")
            elif isinstance(resultados, list):
                print(f"Resultados obtenidos: {len(resultados)} registros")
                if resultados:
                    print(f"Primera fila: {list(resultados[0].keys())[:3]}")
            else:
                print(f"Tipo de resultados: {type(resultados)}")
            state["resultados_sql"] = resultados
            state["ejecucion_exitosa"] = True
            if state["ejecucion_exitosa"]:
                # Fire-and-forget: la indexación en Neo4j es historial
                # secundario, no debe bloquear la respuesta principal. Se
                # detectó un deadlock real (no solo lento) al calcular el
                # embedding desde un thread de asyncio.to_thread en algunos
                # entornos (Windows + torch), que ni siquiera un timeout podía
                # interrumpir porque congelaba el event loop entero. Se
                # desacopla por completo: corre en background, sin esperar.
                asyncio.create_task(self._indexar_consulta_en_background(state.copy()))
            print("=== EJECUCIÓN SQL COMPLETADA CON ÉXITO ===")
            return state
        except Exception as e:
            error_msg = f"Error al ejecutar SQL: {str(e)}"
            print(f"ERROR en ejecución: {error_msg}")
            import traceback
            print(f"Traceback completo:")
            traceback.print_exc()
            state["errores"] = state.get("errores", []) + [error_msg]
            state["ejecucion_exitosa"] = False
            print("=== EJECUCIÓN SQL FALLIDA ===")
            return state

    async def _indexar_consulta_en_background(self, state: State) -> None:
        """Wrapper para correr indexar_consulta() como tarea de background
        (fire-and-forget) sin propagar excepciones no manejadas al loop.
        """
        try:
            await asyncio.wait_for(self.indexar_consulta(state), timeout=15)
        except asyncio.TimeoutError:
            print("Timeout indexando en Neo4j (>15s) en background, se omite")
        except Exception as e:
            print(f"Error en indexación de Neo4j en background: {e}")

    async def indexar_consulta(self, state: State) -> State:
        print("Intentando indexar consulta SQL en Neo4j")
        if not state.get("sql_generado"):
            print("No hay SQL generado para indexar")
            return state
        try:
            if "sql_ast" not in state or not state["sql_ast"]:
                print("Parseando SQL a AST")
                state["sql_ast"] = await asyncio.to_thread(
                    self.config.sql_processor.parse_sql_to_ast,
                    state["sql_generado"]
                )
            print(f"Indexando consulta: {state['pregunta'][:50]}")
            # Timeout defensivo: la indexación en Neo4j es una funcionalidad
            # secundaria (historial de consultas), no debe poder colgar la
            # respuesta principal de NL->SQL si el cálculo de embeddings
            # (torch/sentence-transformers) se traba en algún entorno de
            # ejecución (visto en modo API/uvicorn con asyncio.to_thread).
            try:
                resultado_indexacion = await asyncio.wait_for(
                    asyncio.to_thread(
                        self.config.neo4j_manager.index_sql_query,
                        state["sql_ast"],
                        state["pregunta"],
                        state["sql_generado"]
                    ),
                    timeout=15,
                )
            except asyncio.TimeoutError:
                print("Timeout indexando en Neo4j (>15s), se omite sin bloquear la respuesta")
                resultado_indexacion = None

            if resultado_indexacion:
                print("Consulta indexada exitosamente en Neo4j")
                state["metadata"] = {
                    "neo4j_id": resultado_indexacion.get("id"),
                    "timestamp": resultado_indexacion.get("timestamp")
                }
            else:
                print("La indexación en Neo4j no devolvio resultados")

        except Exception as e:
            error_msg = f"Error indexando consulta: {str(e)}"
            print(error_msg)
            state["errores"] = state.get("errores", []) + [error_msg]
        return state

    async def rechazar_pregunta(self, state: State) -> State:
        decision_seguridad = state.get("decision_seguridad", {})
        tipo = decision_seguridad.get("tipo", "rechazo_seguridad")

        if tipo == "error_transitorio":
            print("Rechazando pregunta por error transitorio del servicio de LLM")
            state["respuesta_natural"] = (
                " El servicio de análisis no está disponible en este momento. "
                "\n\nPor favor reintenta tu consulta en unos instantes."
            )
            error_msg = "Pregunta no procesada: error transitorio del servicio de LLM"
        else:
            print("Rechazando pregunta por motivos de seguridad")
            razon = decision_seguridad.get("razon", decision_seguridad.get("riesgo", "motivos de seguridad"))

            if razon == "motivos de seguridad" and state.get("errores"):
                for error in state["errores"]:
                    if "seguridad" in error.lower() or "riesgo" in error.lower():
                        razon = error
                        break

            state["respuesta_natural"] = (
                f" No puedo procesar tu solicitud debido a {razon}. "
                f"\n\nPor favor formula una consulta diferente que sea de solo lectura (SELECT) "
                f"y no involucre operaciones peligrosas como INSERT, UPDATE, DELETE, DROP, etc."
                f"\n\nSi crees que esto es un error, por contacta con el administrador del sistema."
            )

            error_msg = f"Pregunta rechazada por seguridad: {razon}"

        state["errores"] = state.get("errores", []) + [error_msg]
        state["metadata"] = state.get("metadata", {})
        state["metadata"]["error_tipo"] = tipo

        print(f"Pregunta rechazada: {error_msg}")
        return state

    async def validar_sql(self, state: State) -> State:
        print(f"--- Inicio de validar_sql ---")
        print(f"Intentos de generación de SQL: {state.get('intentos_generacion_sql', 0)}")
        sql_generado = state.get("sql_generado")
        print(f"SQL generado: {sql_generado}")
        if not sql_generado:
            error_msg = "No hay SQL generado para validar"
            print(f"No hay SQL generado: {error_msg}")
            state["errores"] = state.get("errores", []) + [error_msg]
            state["sql_valido"] = False
            return state
        try:
            print("Validando sintaxis SQL")
            validacion = self.config.sql_processor.validate_sql(sql_generado)
            print(f"Resultado de validación: {validacion}")
            if not validacion.get("is_valid", False):
                error_msg = validacion.get("error", "SQL no válido")
                print(f"SQL inválido: {error_msg}")
                state["errores"] = state.get("errores", []) + [error_msg]
                state["sql_valido"] = False
                state["error_validacion"] = error_msg
                return state
            print("SQL validado exitosamente")
            state["sql_valido"] = True
            state["error_validacion"] = None
            return state
        except Exception as e:
            error_msg = f"Error en validación SQL: {str(e)}"
            print(f"Error en validación SQL: {error_msg}")
            state["errores"] = state.get("errores", []) + [error_msg]
            state["sql_valido"] = False
            state["error_validacion"] = str(e)
            return state

def _bootstrap_demo_database():
    """Crea/siembra la base de datos de demo antes de conectar el agente.

    Import dinámico vía importlib: `langgraph dev` carga este archivo por
    ruta de archivo, no como parte de un paquete instalado, así que un
    `from .db_bootstrap import ...` o `from agent.db_bootstrap import ...`
    normal puede fallar según el mecanismo de carga. Resolver por ruta de
    archivo es independiente de cómo se importe este módulo.
    """
    try:
        import importlib.util

        modulo_path = Path(__file__).parent / "db_bootstrap.py"
        spec = importlib.util.spec_from_file_location("db_bootstrap", modulo_path)
        db_bootstrap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(db_bootstrap)
        db_bootstrap.ensure_app_database()
    except Exception as e:
        print(f"[graph.py] No se pudo ejecutar el bootstrap de la DB de demo: {e}")


def setup_graph():
    """Inicializa el agente de forma síncrona al importar el módulo.

    Se ejecuta el `_init_models()` async dentro de un thread separado, con su
    propio event loop nuevo, en vez de correrlo directamente en el thread
    principal. Esto es necesario porque cuando uvicorn recibe la app como
    string (`uvicorn src.agent.api:app`, usado en producción/Render), el
    import del módulo ocurre DENTRO de `asyncio.run(self.serve(...))` — es
    decir, ya hay un event loop corriendo en el thread principal en ese
    momento. Intentar crear y correr un loop nuevo ahí mismo lanza
    "RuntimeError: Cannot run the event loop while another loop is running".
    Al aislar la ejecución en un thread nuevo, el loop que se crea acá nunca
    coincide con el del thread principal, sin importar si ya había uno
    corriendo o no (cubre tanto `python api.py` local, como `langgraph dev`,
    como el Start Command de Render).
    """
    print("1. Configurando grafo")
    _bootstrap_demo_database()
    agent = LangGraphAgent()
    error_holder = {}

    def _run_init():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            print("2. Inicializando modelos.")
            loop.run_until_complete(agent._init_models())
            print("3. Configuración del grafo completada exitosamente")
        except Exception as e:
            print(f"4. Error en setup_graph: {str(e)}")
            error_holder["error"] = e
        finally:
            print("5. Cerrando loop de eventos")
            loop.close()

    thread = threading.Thread(target=_run_init, name="setup_graph-init")
    thread.start()
    thread.join()

    if "error" in error_holder:
        raise error_holder["error"]

    return agent

agent = setup_graph()

_agent_instance = agent


async def get_graph():
    """Devuelve el grafo LangGraph ya compilado.

    Reusa la instancia `agent` inicializada a nivel de módulo (setup_graph()
    ya corrió _init_models() de forma síncrona al importar este archivo), en
    vez de crear y conectar una segunda instancia contra Postgres/Neo4j/Chroma.
    """
    global _agent_instance
    if _agent_instance is None or _agent_instance.graph_instance is None:
        _agent_instance = LangGraphAgent()
        await _agent_instance._init_models()
    return _agent_instance.graph_instance


async def main():
    if len(sys.argv) < 2:
        # Modo consola por defecto (tu código actual)
        print("Chat iniciado. Escribe 'salir' para terminar.")
        agent = LangGraphAgent()
        await agent._init_models()
        
        while True:
            user_input = input("Usuario: ")
            if user_input.lower() in ["salir", "exit", "quit"]:
                print("Cerrando chat.")
                break
            response = await agent.stream_response(user_input)
            print("\n" + "="*50)
            print("Respuesta completa:", response)
            print("="*50 + "\n")
        return

    comando = sys.argv[1]

    if comando == "dev":
        print("Ejecutando LangGraph dev...")
        subprocess.run([sys.executable, "-m", "langgraph", "dev"])

    elif comando == "api":
        print("Ejecutando API en puerto 8000...")
        import uvicorn
        uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)

    elif comando == "console":
        print("Chat iniciado. Escribe 'salir' para terminar.")
        agent = LangGraphAgent()
        await agent._init_models()
        
        while True:
            user_input = input("Usuario: ")
            if user_input.lower() in ["salir", "exit", "quit"]:
                print("Cerrando chat.")
                break
            response = await agent.stream_response(user_input)
            print("\n" + "="*50)
            print("Respuesta completa:", response)
            print("="*50 + "\n")

    else:
        print("Comando no válido. Usar: dev, api o console")

if __name__ == "__main__":
    asyncio.run(main())