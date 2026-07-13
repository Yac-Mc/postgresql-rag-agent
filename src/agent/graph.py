import asyncio
import json
import os

# Debe fijarse ANTES de importar torch/sentence-transformers (más abajo):
# evita un deadlock real en Windows cuando el modelo de embeddings corre
# inferencia desde un thread distinto al que lo cargó (típico al usar
# asyncio.to_thread para no bloquear el event loop de FastAPI/uvicorn).
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import re
import uuid
import sys
from datetime import datetime
from pathlib import Path
from typing import Annotated, Dict, List, Optional, Union
from typing_extensions import TypedDict, NotRequired
from urllib.parse import urlparse

import chromadb
import numpy as np
import psycopg2
from neo4j import GraphDatabase
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker

from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition


def obtener_ddl_dinamico(engine) -> str:
    """Introspecciona el esquema real de la base de datos Postgres conectada
    y arma un DDL legible para el LLM. Se ejecuta en runtime (sin cache) para
    que el agente funcione contra cualquier base de datos Postgres, no solo
    contra un dominio fijo.
    """
    inspector = inspect(engine)
    partes = ["# ESQUEMA DE BASE DE DATOS (introspeccion en vivo)", ""]

    esquema = os.getenv("APP_DB_SCHEMA", "public")
    tablas = inspector.get_table_names(schema=esquema)

    for tabla in sorted(tablas):
        partes.append(f"## {tabla}")
        pk_columnas = set(
            inspector.get_pk_constraint(tabla, schema=esquema).get("constrained_columns") or []
        )
        for columna in inspector.get_columns(tabla, schema=esquema):
            nombre = columna["name"]
            tipo = str(columna["type"])
            marcas = []
            if not columna.get("nullable", True):
                marcas.append("NOT NULL")
            if nombre in pk_columnas:
                marcas.append("PK")
            sufijo = f" [{'] ['.join(marcas)}]" if marcas else ""
            partes.append(f"- {nombre} ({tipo}){sufijo}")

        foreign_keys = inspector.get_foreign_keys(tabla, schema=esquema)
        if foreign_keys:
            for fk in foreign_keys:
                columnas_origen = ", ".join(fk.get("constrained_columns") or [])
                tabla_destino = fk.get("referred_table")
                columnas_destino = ", ".join(fk.get("referred_columns") or [])
                partes.append(f"  FK: {columnas_origen} -> {tabla_destino}.{columnas_destino}")

        partes.append("")

    if not tablas:
        partes.append("(No se encontraron tablas en el esquema. Verifica APP_DB_SCHEMA y que la base de datos tenga datos.)")

    return "\n".join(partes)


API_KEY_GEMINI = os.getenv("GEMINI_API_KEY", "")


class State(TypedDict):
    messages: Annotated[List, add_messages]
    pregunta: str
    contexto: NotRequired[str]
    embedding: NotRequired[List[float]]
    sql_generado: NotRequired[str]
    sql_ast: NotRequired[Dict]
    resultados_sql: NotRequired[List[Dict]]
    resultados_neo4j: NotRequired[List[Dict]]
    respuesta_natural: NotRequired[str]
    errores: List[str]
    ejecucion_exitosa: NotRequired[bool]
    intentos_ejecucion: NotRequired[int]
    decision_seguridad: NotRequired[Dict]
    metadata: NotRequired[Dict]
    ddl: NotRequired[str]
    sql_valido: NotRequired[bool] 
    metricas_busqueda: NotRequired[Dict]

class Neo4jGraph:
    def __init__(self, url: str, username: str, password: str):
        self.driver = GraphDatabase.driver(url, auth=(username, password))
        print("Neo4jGraph Conexión a Neo4j establecida exitosamente")

    def close(self):
        self.driver.close()

    def query(self, query: str, parameters: Dict = None):
        try:
            with self.driver.session() as session:
                result = session.run(query, parameters or {})
                return [record.data() for record in result]
        except Exception as e:
            print(f"Error en consulta Neo4j: {str(e)}")
            raise

class ChatbotConfig:
    def __init__(self):
        print("ChatbotConfig Inicializando configuración")
        self._ddl_cache = None
        self._ddl_cache_llm = None
        self.base_dir = Path(__file__).parent.parent
        self.chroma_path ="chroma_db_v2"
        os.makedirs(self.chroma_path, exist_ok=True)
        print(f"ChatbotConfig Directorio ChromaDB: {self.chroma_path}")
        self._initialized = False
        self.sql_processor = SQLProcessor()

    async def initialize(self):
        if self._initialized:
            return
        try:
            await self._init_chromadb()
            await self._init_neo4j()
            await self._init_postgresql()
            await self._init_models()
            self.sql_processor.config = self
            self._initialized = True
            print("Chatbot Inicializado")
        except Exception as e:
            print(f"Error en inicialización: {str(e)}")
            raise

    async def _init_chromadb(self):
        self.client = await asyncio.to_thread(
            chromadb.PersistentClient,
            path=self.chroma_path
        )
        self.collection = await asyncio.to_thread(
            lambda: self.client.get_or_create_collection(name="mi_base_vectorizada")
        )
        count = await asyncio.to_thread(
            lambda: self.collection.count()
        )
        print("ChatbotConfig _init_chromadb Conexión a ChromaDB establecida exitosamente")

    async def calcular_metricas_busqueda(self, pregunta: str, resultados_chroma: Dict = None, k: int = 4) -> Dict[str, float]:
        try:
            print(f"[calcular_metricas_busqueda] Iniciando cálculo para pregunta: '{pregunta}'")
            print(f"[calcular_metricas_busqueda] Parámetros - k: {k}, resultados_chroma proporcionados: {resultados_chroma is not None}")
            
            if resultados_chroma is None:
                print("[1] No hay resultados proporcionados, calculando embedding y buscando en ChromaDB...")
                pregunta_embedding = await asyncio.to_thread(
                    self.embedding_model.encode, [pregunta], convert_to_tensor=False
                )
                print(f"[1.1] Embedding calculado, dimensión: {len(pregunta_embedding.tolist()[0]) if pregunta_embedding is not None else 'N/A'}")
                
                resultados_chroma = await asyncio.to_thread(
                    self.collection.query,
                    query_embeddings=[pregunta_embedding.tolist()[0]],
                    n_results=k,
                    include=["embeddings", "documents", "metadatas", "distances"]
                )
                print("[1.2] Búsqueda en ChromaDB completada")
            else:
                print("[1] Reutilizando resultados de ChromaDB existentes")
                print(f"[1.1] Resultados recibidos - documentos: {len(resultados_chroma.get('documents', [[]])[0]) if resultados_chroma else 0}")
            
            print(f"[2] Documentos encontrados: {len(resultados_chroma['documents'][0]) if resultados_chroma and 'documents' in resultados_chroma and resultados_chroma['documents'] else 0}")
            
            if not resultados_chroma or not resultados_chroma["documents"]:
                print("[3] No hay resultados, retornando métricas en 0.0")
                return {"recall@k": 0.0, "similitud_promedio": 0.0, "exactitud": 0.0}
            
            print("[4] Iniciando cálculo de métricas en paralelo")
            print("[4.1] Creando tareas para recall, similitud y exactitudes")

            recall_task = self._calcular_recall_k(resultados_chroma, pregunta)
            similitud_task = self._calcular_similitud_promedio(resultados_chroma)
            exactitud_embed_task = self._calcular_exactitud_documentosEmbeddigs(resultados_chroma, pregunta)
            exactitud_palabras_task = self._calcular_exactitud_documentosConcidenciaPalabras(resultados_chroma, pregunta)

            print("[4.2] Ejecutando tareas con asyncio.gather...")
            recall, similitud, exactitud_embed, exactitud_palabras = await asyncio.gather(
                recall_task, similitud_task, exactitud_embed_task, exactitud_palabras_task,
                return_exceptions=True  
            )

            print("[5] Procesando resultados de las tareas...")
            if isinstance(recall, Exception):
                print(f"[ERROR] En recall: {recall}")
                recall = 0.0
            else:
                print(f"[5.1] Recall calculado: {recall}")
                
            if isinstance(similitud, Exception):
                print(f"[ERROR] En similitud: {similitud}")
                similitud = 0.0
            else:
                print(f"[5.2] Similitud calculada: {similitud}")
                
            if isinstance(exactitud_embed, Exception):
                print(f"[ERROR] En exactitud embeddings: {exactitud_embed}")
                exactitud_embed = 0.0
            else:
                print(f"[5.3] Exactitud embeddings calculada: {exactitud_embed}")
                
            if isinstance(exactitud_palabras, Exception):
                print(f"[ERROR] En exactitud palabras: {exactitud_palabras}")
                exactitud_palabras = 0.0
            else:
                print(f"[5.4] Exactitud palabras calculada: {exactitud_palabras}")

            print("[6] Combinando métricas en diccionario final...")
            metricas = {
            "recall@k": recall,
            "similitud_promedio": similitud,
            "exactitud_embeddings": exactitud_embed,
            "exactitud_palabras": exactitud_palabras,
            "exactitud_promedio": round((exactitud_embed + exactitud_palabras) / 2, 3) if (exactitud_embed + exactitud_palabras) > 0 else 0.0
            }
          
            
            print("[7] Métricas finales calculadas:")
            print(
                f"{metricas['recall@k']:.3f}\t"
                f"{metricas['similitud_promedio']:.3f}\t"
                f"{metricas['exactitud_embeddings']:.3f}\t"
                f"{metricas['exactitud_palabras']:.3f}\t"
                f"{metricas['exactitud_promedio']:.3f}"
            )
            print(
                "\t".join([
                    f"{metricas['recall@k']:.5f}",
                    f"{metricas['similitud_promedio']:.5f}",
                    f"{metricas['exactitud_embeddings']:.5f}",
                    f"{metricas['exactitud_palabras']:.5f}",
                    f"{metricas['exactitud_promedio']:.5f}"
                ])
            )
            print("[8] Retornando resultados...")
            return metricas

        except Exception as e:
            print(f"[ERROR calcular_metricas_busqueda] Error crítico: {str(e)}")
            print(f"[ERROR] Tipo de excepción: {type(e).__name__}")
            import traceback
            print(f"[ERROR] Traceback completo: {traceback.format_exc()}")
            print("[ERROR] Retornando métricas por defecto (0.0)")
            return {
                "recall@k": 0.0, 
                "similitud_promedio": 0.0, 
                "exactitud_embeddings": 0.0,
                "exactitud_palabras": 0.0,
                "exactitud_promedio": 0.0
            }
        
    async def _calcular_exactitud_documentosConcidenciaPalabras(self, resultados: Dict, pregunta: str) -> float:
        try:
            documentos = resultados["documents"][0][:3]
            if not documentos:
                return 0.0
            
            palabras_pregunta = set(pregunta.lower().split())
            palabras_relevantes = palabras_pregunta - {'qué', 'cómo', 'cuál', 'dónde', 'cuándo', 'por', 'para', 'con', 'de', 'en'}
            
            if not palabras_relevantes:
                return 0.5  
            
            exactitudes = []
            for doc in documentos:
                doc_lower = doc.lower()
                palabras_encontradas = sum(1 for palabra in palabras_relevantes if palabra in doc_lower)
                exactitud_doc = palabras_encontradas / len(palabras_relevantes)
                exactitudes.append(exactitud_doc)
            
            # Promedio de exactitud de los documentos
            exactitud = sum(exactitudes) / len(exactitudes) if exactitudes else 0.0
            
            return round(min(exactitud, 1.0), 3)

        except Exception as e:
            print(f"Error calculando exactitud: {str(e)}")
            return 0.0

    async def _calcular_recall_k(self, resultados: Dict, pregunta: str, relevancia_umbral: float = 0.7) -> float:
        try:
            print(f"[_calcular_recall_k] Iniciando cálculo de recall para: '{pregunta[:50]}...'")
            
            documentos_relevantes = await self._obtener_documentos_relevantes(pregunta)
            print(f"[Recall] Documentos relevantes encontrados: {len(documentos_relevantes)}")
            
            documentos_recuperados = resultados["documents"][0]
            print(f"[Recall] Documentos recuperados en búsqueda: {len(documentos_recuperados)}")

            if not documentos_relevantes:
                print("[Recall] No hay documentos relevantes, retornando 0.0")
                return 0.0

            relevantes_recuperados = sum(1 for doc in documentos_recuperados
                                    if doc in documentos_relevantes)
            
            print(f"[Recall] Documentos relevantes que fueron recuperados: {relevantes_recuperados}")

            recall = relevantes_recuperados / len(documentos_relevantes)
            print(f"[Recall] Cálculo: {relevantes_recuperados} / {len(documentos_relevantes)} = {recall}")
            
            recall_final = min(round(recall, 3), 1.0)
            print(f"[Recall] Resultado final: {recall_final}")
            
            return recall_final

        except Exception as e:
            print(f"Error calculando recall: {str(e)}")
            return 0.0

    async def _calcular_similitud_promedio(self, resultados: Dict) -> float:
        try:
            if "distances" not in resultados or not resultados["distances"]:
                return 0.0

            distancias = resultados["distances"][0]
            similitudes = [1 - (dist / max(distancias)) if max(distancias) > 0 else 1 - dist
                        for dist in distancias]

            return round(sum(similitudes) / len(similitudes), 3) if similitudes else 0.0

        except Exception as e:
            print(f"Error calculando similitud: {str(e)}")
            return 0.0

    async def _calcular_exactitud_documentosEmbeddigs(self, resultados: Dict, pregunta: str, pregunta_embedding=None) -> float:
        try:
            from sklearn.metrics.pairwise import cosine_similarity
            
            documentos = resultados["documents"][0][:3]
            if not documentos:
                return 0.0
            
            if pregunta_embedding is None:
                pregunta_embedding = await asyncio.to_thread(
                    self.embedding_model.encode, [pregunta], convert_to_tensor=True
                )
            
            if "embeddings" in resultados and resultados["embeddings"]:
                doc_embeddings = np.array(resultados["embeddings"][0][:3])  
            else:
                doc_embeddings = await asyncio.to_thread(
                    self.embedding_model.encode, documentos, convert_to_tensor=True
                )
            
            similitudes = cosine_similarity(pregunta_embedding, doc_embeddings)
            exactitud = float(similitudes.mean())
            
            return round(min(exactitud, 1.0), 3)

        except Exception as e:
            print(f"Error calculando exactitud: {str(e)}")
            return 0.0

    async def _obtener_documentos_relevantes(self, pregunta: str) -> List[str]:
        try:
            print(f"[_obtener_documentos_relevantes] Iniciando búsqueda vectorial para: '{pregunta[:50]}...'")
            print("[Relevantes] Calculando embedding de la pregunta")
            pregunta_embedding = await asyncio.to_thread(
                self.embedding_model.encode, [pregunta], convert_to_tensor=False
            )
            print(f"[Relevantes] Embedding calculado. Dimensiones: {pregunta_embedding.shape}")
            print("[Relevantes] Realizando búsqueda vectorial en ChromaDB...")
            resultados = await asyncio.to_thread(
                self.collection.query,
                query_embeddings=[pregunta_embedding.tolist()[0]],
                n_results=4,  
                include=["documents", "distances"]
            )
           
            if not resultados or not resultados["documents"]:
                print("[Relevantes] No se encontraron documentos en la búsqueda vectorial")
                return []
            
            documentos_recuperados = resultados["documents"][0]
            distancias = resultados["distances"][0] if "distances" in resultados else []
            
            print(f"[Relevantes] Documentos recuperados: {len(documentos_recuperados)}")
            print(f"[Relevantes] Distancias: {distancias[:5]}...")  
            documentos_relevantes = []
            for i, (doc, distancia) in enumerate(zip(documentos_recuperados, distancias)):
                similitud = 1 - (distancia / max(distancias)) if distancias and max(distancias) > 0 else 1
                if similitud >= 0.3:  
                    documentos_relevantes.append(doc)
                    if len(documentos_relevantes) <= 3:
                        print(f"[Relevantes] Documento {i+1} - Similitud: {similitud:.3f}: '{doc[:80]}...'")
            
            print(f"[Relevantes] Documentos relevantes después de filtro: {len(documentos_relevantes)}")
            
            resultado_final = documentos_relevantes[:10]
            print(f"[Relevantes] Resultado final (limitado a {len(resultado_final)} documentos)")
            
            return resultado_final

        except Exception as e:
            print(f"[ERROR _obtener_documentos_relevantes] Error: {str(e)}")
            import traceback
            print(f"[ERROR] Traceback: {traceback.format_exc()}")
            return []

    async def _init_neo4j(self):
        neo4j_uri = os.getenv("NEO4J_URI", "neo4j://localhost:7687")
        neo4j_user = os.getenv("NEO4J_USER", "neo4j")
        neo4j_password = os.getenv("NEO4J_PASSWORD")
        if not neo4j_password:
            raise ValueError("NEO4J_PASSWORD no está definida en el entorno (.env)")

        self.neo4j_graph = await asyncio.to_thread(
            Neo4jGraph,
            url=neo4j_uri,
            username=neo4j_user,
            password=neo4j_password
        )
        self.neo4j_manager = Neo4jManager(self.neo4j_graph, self)
        print("Manager de Neo4j inicializado exitosamente")

    async def _init_postgresql(self):
        self.database_url = os.getenv("DATABASE_URL")
        if not self.database_url:
            raise ValueError("DATABASE_URL no está definida en el entorno (.env)")

        if not await self.verificar_conexion_postgresql():
            raise ConnectionError("No se pudo establecer conexión con PostgreSQL")

        self.engine = await asyncio.to_thread(
            create_engine,
            self.database_url
        )
        self.SessionLocal = await asyncio.to_thread(
            sessionmaker,
            autocommit=False,
            autoflush=False,
            bind=self.engine
        )

        def test_connection():
            with self.engine.connect() as conn:
                return True

        if await asyncio.to_thread(test_connection):
            print("_init_neo4j _init_postgresql Conexión a PostgreSQL establecida exitosamente")

        self.ddl = await asyncio.to_thread(obtener_ddl_dinamico, self.engine)
        print(f"_init_neo4j _init_postgresql Esquema DDL obtenido por introspección dinámica ({len(self.ddl.splitlines())} líneas)")

    async def verificar_conexion_postgresql(self) -> bool:
        try:
            def conectar():
                conn = psycopg2.connect(self.database_url, connect_timeout=10)
                conn.close()
                return True

            resultado = await asyncio.to_thread(conectar)

            if resultado:
                print("_init_neo4j verificar_conexion_postgresql Verificación de conexión PostgreSQL exitosa")
                return True
        except Exception as e:
            print(f"Error verificando conexión PostgreSQL: {str(e)}")

        return False

    async def _init_models(self):
        self.embedding_model = await asyncio.to_thread(
            SentenceTransformer,
            'sentence-transformers/all-MiniLM-L6-v2'
        )
        print("_init_neo4j _init_models Modelo de embeddings cargado exitosamente")
        print("_init_neo4j_init_models Configuración de modelos completada (Gemini se usará directamente)")

    async def _obtener_ddl(self) -> str:
        # Introspección dinámica en runtime, sin cache: refleja el estado
        # real de la base de datos conectada en cada llamada.
        return await asyncio.to_thread(obtener_ddl_dinamico, self.engine)

    def sync_execute_query(self, sql_query: str) -> tuple[Union[List[Dict], None], Optional[str]]:
        try:
            with self.engine.connect() as conn:
                print(f"Ejecutando consulta SQL: {sql_query[:200]}")
                conn.execute(text("SET statement_timeout TO 60000"))
                result = conn.execute(text(sql_query))
                print("Consulta SQL ejecutada exitosamente")
                return [dict(row._mapping) for row in result], None

        except Exception as e:
            error_msg = str(e).strip().replace('\n', ' | ')
            if hasattr(e, 'orig') and hasattr(e.orig, 'pgerror'):
                error_msg = e.orig.pgerror.split('|')[0].strip()
            print(f"Error en ejecución de consulta SQL: {error_msg}")
            return None, error_msg

    async def chat_gemini(self, pregunta: str, contexto_rag: str = "") -> str:
        print("Invocando Gemini (via LangChain) para generar SQL")

        if not contexto_rag:
            rag_system = SQLRAGSystem()
            contexto_rag = rag_system.obtener_contexto_rag(pregunta)

        ddl_actual = await self._obtener_ddl()

        prompt_template = ChatPromptTemplate.from_messages([
            ("system",
             "Eres un asistente especializado en generar consultas SQL para PostgreSQL "
             "a partir de preguntas en lenguaje natural. Tienes acceso al siguiente esquema "
             "de base de datos:\n\n{ddl}\n\n{contexto_rag}\n\n"
             "INSTRUCCIONES:\n"
             "- Responde ÚNICAMENTE con la consulta SQL válida\n"
             "- No incluyas explicaciones adicionales, comentarios o texto fuera del SQL\n"
             "- Usa el esquema proporcionado para generar consultas precisas\n"
             "- Si la pregunta es sobre contar registros, usa COUNT(*)\n"
             "- Si es sobre listar datos, usa SELECT con las columnas apropiadas\n"
             "- Para consultas complejas, usa JOINs entre las tablas relacionadas"),
            ("human", "{pregunta}"),
        ])

        def _invocar_sync():
            # Construcción del cliente + invocación juntas en el mismo thread:
            # si la construcción del cliente Gemini también hace I/O síncrona
            # (lectura de credenciales/certificados), envolver solo el .invoke()
            # no alcanza para escapar del detector de bloqueo de "langgraph dev".
            llm = ChatGoogleGenerativeAI(model="gemini-flash-latest", google_api_key=API_KEY_GEMINI)
            chain = prompt_template | llm
            return chain.invoke({
                "ddl": ddl_actual,
                "contexto_rag": contexto_rag or "",
                "pregunta": pregunta,
            })

        try:
            resultado = await asyncio.to_thread(_invocar_sync)
            respuesta = resultado.content.strip()
            print("Respuesta de Gemini recibida")
            return respuesta
        except Exception as e:
            error_msg = f"Error invocando Gemini: {str(e)}"
            print(error_msg)
            return error_msg


class SQLRAGSystem:
    def __init__(self):
        self.model = SentenceTransformer('all-MiniLM-L6-v2')

    def get_connection(self):
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            raise ValueError("DATABASE_URL no está definida en el entorno (.env)")
        # sslmode NO se hardcodea: depende del servidor (Postgres local en Docker
        # no tiene SSL habilitado; una instancia cloud puede requerirlo). Si hace
        # falta, se agrega como query param en el propio DATABASE_URL
        # (?sslmode=require), no en código.
        return psycopg2.connect(database_url)

    def buscar_instrucciones_similares(self, pregunta_usuario, top_k=5, umbral_similitud=0.3):
        conexion = self.get_connection()

        try:
            cursor = conexion.cursor()
            embedding_consulta = self.model.encode(pregunta_usuario)
            embedding_list = embedding_consulta.tolist()

            cursor.execute("""
                SELECT
                    pregunta,
                    instruccion,
                    1 - (embedding <=> %s::vector) as similitud
                FROM instrucciones_sql
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> %s::vector
                LIMIT %s
            """, (embedding_list, embedding_list, top_k))

            resultados = cursor.fetchall()

            resultados_filtrados = [
                (pregunta, instruccion, similitud)
                for pregunta, instruccion, similitud in resultados
                if similitud >= umbral_similitud
            ]

            return resultados_filtrados

        except Exception as e:
            print(f"Error en busqueda RAG: {e}")
            return []
        finally:
            cursor.close()
            conexion.close()

    def obtener_contexto_rag(self, pregunta_usuario, top_k=3):
        resultados = self.buscar_instrucciones_similares(pregunta_usuario, top_k)

        if not resultados:
            return ""

        contexto = "\n\n--- EJEMPLOS SIMILARES ENCONTRADOS (RAG) ---\n"

        for i, (pregunta, instruccion, similitud) in enumerate(resultados, 1):
            contexto += f"\nEjemplo {i} (Similitud: {similitud:.3f}):\n"
            contexto += f"PREGUNTA: {pregunta}\n"
            contexto += f"SQL: {instruccion}\n"

        contexto += f"\nPREGUNTA ACTUAL: {pregunta_usuario}\n"
        contexto += "Basándote en los ejemplos similares, genera la consulta SQL apropiada:"

        return contexto


class Neo4jManager:
    def __init__(self, graph: Neo4jGraph, config: ChatbotConfig = None):
        self.graph = graph
        self.config = config
        print("Neo4jManager inicializado exitosamente")

    def index_sql_query(self, sql_ast: Dict, question: str, sql: str) -> Optional[Dict]:
        print("Iniciando indexacion de consulta SQL en Neo4j")
        try:
            print("Verificando configuracion y modelo de embeddings")
            embedding = None
            if self.config and hasattr(self.config, 'embedding_model'):
                print("Generando embedding para la pregunta")
                embedding = self.config.embedding_model.encode([question]).tolist()[0]
                print(f"Embedding generado: {len(embedding)} dimensiones")

            print("Preparando datos para la consulta Neo4j")
            intention = sql_ast.get("intention", "unknown")
            select_columns = sql_ast.get("select", [])
            tables = sql_ast.get("from", [])

            print(f"Datos a indexar:")
            print(f"- Pregunta: {question[:100]}")
            print(f"- Intencion: {intention}")
            print(f"- Tablas: {tables}")
            print(f"- Columnas SELECT: {select_columns[:5]}{'...' if len(select_columns) > 5 else ''}")
            print(f"- SQL length: {len(sql)} caracteres")
            print(f"- SQL preview: {sql[:200]}...")

            query = """
            MERGE (q:SQLQuery {id: $id})
            SET q.question = $question,
                q.generated_sql = $sql,
                q.intention = $intention,
                q.select_columns = $select,
                q.source_tables = $tables,
                q.embedding = $embedding,
                q.timestamp = datetime()
            WITH q
            UNWIND $tables AS table
            MERGE (t:Table {name: table})
            MERGE (q)-[r:QUERIES]->(t)
            RETURN q
            """

            print("Ejecutando consulta Cypher en Neo4j")
            query_params = {
                "id": str(uuid.uuid4()),
                "question": question,
                "sql": sql,
                "intention": intention,
                "select": select_columns,
                "tables": tables,
                "embedding": embedding
            }

            result = self.graph.query(query, query_params)

            print(f"Consulta Neo4j ejecutada - resultados obtenidos: {len(result) if result else 0}")

            if result:
                print("Indexacion exitosa en Neo4j")
                returned_data = result[0] if result else None
                if returned_data and 'q' in returned_data:
                    print(f"Nodo creado con ID: {returned_data['q'].get('id', 'desconocido')}")
                return returned_data
            else:
                print("Advertencia: La consulta Neo4j no devolvio resultados")
                return None

        except Exception as e:
            print(f"ERROR en indexacion Neo4j: {str(e)}")
            return None

    async def semantic_search(self, question: str, limit: int = 5) -> List[Dict]:
        return await asyncio.to_thread(self._sync_semantic_search, question, limit)

    def _sync_semantic_search(self, question: str, limit: int) -> List[Dict]:
        try:
            question_embedding = self.config.embedding_model.encode([question])
            query = """
            CALL db.index.vector.queryNodes('question_embeddings', $limit, $embedding)
            YIELD node, score
            RETURN node.question AS question,
                node.generated_sql AS sql,
                node.intention AS intention,
                node.source_tables AS tables,
                score
            ORDER BY score DESC
            """
            result = self.graph.query(query, {
                "embedding": question_embedding.tolist()[0],
                "limit": limit
            })
            print(f"Búsqueda semántica vectorial completada. Resultados: {len(result)}")
            if not result:
                print("No hay resultados semánticos, usando búsqueda textual como fallback")
                return self._sync_semantic_search_fallback(question, limit)

            return result

        except Exception as e:
            print(f"Error en búsqueda semántica vectorial: {e}")
            return self._sync_semantic_search_fallback(question, limit)

    def _sync_semantic_search_fallback(self, question: str, limit: int) -> List[Dict]:
        try:
            query = """
            MATCH (q:SQLQuery)
            WHERE q.question CONTAINS $query
            OR q.intention CONTAINS $query
            OR ANY(col IN q.select_columns WHERE col CONTAINS $query)
            RETURN q.question AS question,
                q.generated_sql AS sql,
                q.intention AS intention,
                q.source_tables AS tables
            ORDER by q.timestamp DESC
            LIMIT $limit
            """
            result = self.graph.query(query, {
                "query": question,
                "limit": limit
            })
            print(f"Búsqueda textual fallback completada. Resultados: {len(result)}")
            return result
        except Exception as e:
            print(f"Error en búsqueda textual fallback: {e}")
            return []

class SQLProcessor:
    def __init__(self, config: Optional[ChatbotConfig] = None):
        print("SQLProcessor inicializado")
        self.config = config

    def validate_sql(self, sql: str) -> Dict:
        try:
            sql_lower = sql.lower().strip()
            if not sql_lower.startswith("select"):
                return {
                    "is_valid": False,
                    "error": "Solo se permiten consultas SELECT"
                }
            dangerous_keywords = ["insert", "update", "delete", "drop", "truncate", "alter", "create"]
            for keyword in dangerous_keywords:
                if f" {keyword} " in f" {sql_lower} ":
                    return {
                        "is_valid": False,
                        "error": f"Consulta contiene operación peligrosa: {keyword.upper()}"
                    }
            if "from" not in sql_lower:
                return {
                    "is_valid": False,
                    "error": "Consulta SQL incompleta: falta cláusula FROM"
                }

            return {"is_valid": True}
        except Exception as e:
            return {
                "is_valid": False,
                "error": f"Error validando SQL: {str(e)}"
            }

    def parse_sql_to_ast(self, sql: str) -> Dict:
        try:
            sql_lower = sql.lower()
            ast = {
                "intention": "unknown",
                "select": [],
                "from": [],
                "where": [],
                "join": []
            }

            if "select" in sql_lower and "from" in sql_lower:
                select_part = sql_lower.split("select")[1].split("from")[0]
                ast["select"] = [col.strip() for col in select_part.split(",") if col.strip()]
            if "from" in sql_lower:
                from_part = sql_lower.split("from")[1]
                if "where" in from_part:
                    from_part = from_part.split("where")[0]
                if "join" in from_part:
                    from_part = from_part.split("join")[0]

                ast["from"] = [table.strip() for table in from_part.split(",") if table.strip()]
            if "join" in sql_lower:
                join_parts = sql_lower.split("join")[1:]
                for part in join_parts:
                    if "on" in part:
                        join_table = part.split("on")[0].strip()
                        ast["join"].append(join_table)
            print(f"SQL parseado a AST: {ast}")
            return ast

        except Exception as e:
            print(f"Error parseando SQL to AST: {str(e)}")
            return {
                "intention": "error",
                "select": [],
                "from": [],
                "where": [],
                "join": [],
                "error": str(e)
            }

    def execute_sql(self, sql_query: str) -> tuple[Union[List[Dict], None], Optional[str]]:
        try:
            if not self.config or not hasattr(self.config, 'engine'):
                error_msg = "Configuración de base de datos no disponible"
                print(error_msg)
                return None, error_msg

            with self.config.engine.connect() as conn:
                print(f"\nEjecutando: {sql_query[:800]}")

                conn.execute(text("SET statement_timeout TO 10000"))

                result = conn.execution_options(stream_results=True).execute(text(sql_query))

                chunk_size = 1000
                all_results = []
                while True:
                    chunk = result.fetchmany(chunk_size)
                    if not chunk:
                        break
                    all_results.extend([dict(row._mapping) for row in chunk])

                    if len(all_results) >= 200:
                        print(f"Consulta truncada a 200 registros de {len(all_results)+chunk_size}+")
                        break

                print(f"Consulta ejecutada exitosamente. {len(all_results)} registros obtenidos")
                return all_results, None

        except Exception as e:
            error_msg = str(e).strip().replace('\n', ' | ')
            if hasattr(e, 'orig') and hasattr(e.orig, 'pgerror'):
                error_msg = e.orig.pgerror.split('|')[0].strip()

            if "timeout" in error_msg.lower() or "statement_timeout" in error_msg.lower():
                error_msg = "La consulta excedió el tiempo máximo de ejecución (10 segundos)"
                print(f"Timeout en ejecución SQL: {error_msg}")
            else:
                print(f"Error ejecutando SQL: {error_msg}")

            print(f"¡Error en ejecución! {error_msg}")
            return None, error_msg

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
            if state.get("errores") and any("seguridad" in error.lower() for error in state["errores"]):
                print("Consulta rechazada por seguridad, yendo a rechazar_pregunta")
                return "rechazar_pregunta"
            pregunta = state.get("pregunta", "").lower()
            sql_keywords = ["select", "lista", "cuántos", "cuantas", "cuántas", "mostrar", "buscar", "encontrar", "consultar"]
            if any(keyword in pregunta for keyword in sql_keywords):
                print("Consulta parece requerir SQL, procediendo a buscar contexto")
                return "buscar_contexto"
            else:
                print("Consulta no parece requerir SQL, procediendo a buscar contexto")
                return "buscar_contexto"

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
                        "riesgo": "alto"
                    }
                    state["errores"].append("Error en análisis de seguridad: respuesta no válida")
            except Exception as e:
                print(f"Error en llamada a Gemini: {str(e)}")
                state["decision_seguridad"] = {
                    "es_segura": False,
                    "razon": "error en el análisis de seguridad",
                    "riesgo": "alto"
                }
                state["errores"].append(f"Error en análisis de seguridad: {str(e)}")

        except Exception as e:
            error_msg = f"Error en análisis de seguridad: {str(e)}"
            print(f"EXCEPCIÓN CAPTURADA: {error_msg}")
            state["errores"].append(error_msg)
            state["decision_seguridad"] = {
                "es_segura": False,
                "razon": "error en el análisis de seguridad",
                "riesgo": "alto"
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
            
            # print("Buscando consultas similares en Neo4j...")
            # 
            # # COMENTADO: Función para Neo4j
            # def buscar_neo4j_completo(pregunta: str, limit: int = 3):
            #     """Función síncrona para buscar en Neo4j"""
            #     try:
            #         # Usar el método síncrono del manager
            #         return self.config.neo4j_manager._sync_semantic_search(pregunta, limit)
            #     except Exception as e:
            #         print(f"Error en buscar_neo4j_completo: {str(e)}")
            #         return []
            # 
            # # COMENTADO: Ejecutar búsqueda Neo4j en hilo separado
            # similares = await asyncio.to_thread(
            #     buscar_neo4j_completo,
            #     state["pregunta"],
            #     3  # limit=3
            # )
            # 
            # neo4j_count = len(similares) if similares else 0
            # print(f"Consultas similares Neo4j encontradas: {neo4j_count}")
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
        print("Rechazando pregunta por motivos de seguridad")
        decision_seguridad = state.get("decision_seguridad", {})
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
    print("1. Configurando grafo")
    _bootstrap_demo_database()
    agent = LangGraphAgent()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        print("2. Inicializando modelos.")
        loop.run_until_complete(agent._init_models())
        print("3. Configuración del grafo completada exitosamente")
    except Exception as e:
        print(f"4. Error en setup_graph: {str(e)}")
        raise
    finally:
        print("5. Cerrando loop de eventos")
        loop.close()
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