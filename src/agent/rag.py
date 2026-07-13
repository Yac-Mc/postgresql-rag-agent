import os

import psycopg2
from sentence_transformers import SentenceTransformer


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
