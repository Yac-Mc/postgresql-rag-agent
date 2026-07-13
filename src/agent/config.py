import asyncio
import os
from pathlib import Path
from typing import Dict, List, Optional, Union

import chromadb
import numpy as np
import psycopg2
from sentence_transformers import SentenceTransformer
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from langchain_core.prompts import ChatPromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI

from .neo4j_manager import Neo4jGraph, Neo4jManager
from .rag import SQLRAGSystem
from .sql_processing import SQLProcessor, obtener_ddl_dinamico

API_KEY_GEMINI = os.getenv("GEMINI_API_KEY", "")


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
