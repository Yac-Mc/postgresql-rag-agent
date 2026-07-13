import json
import os
import re
from typing import Dict, List, Optional, Union

from sqlalchemy import inspect, text


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


class SQLProcessor:
    def __init__(self, config: Optional["ChatbotConfig"] = None):
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
