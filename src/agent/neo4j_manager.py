import asyncio
import uuid
from typing import Dict, List, Optional, TYPE_CHECKING

from neo4j import GraphDatabase

if TYPE_CHECKING:
    from .config import ChatbotConfig  # import-time no-op, type-checker only


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


class Neo4jManager:
    def __init__(self, graph: Neo4jGraph, config: "ChatbotConfig" = None):
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
