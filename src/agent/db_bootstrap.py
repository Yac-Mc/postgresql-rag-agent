"""Bootstrap idempotente de una base de datos de demo en Postgres.

Crea (si no existe) la base de datos indicada en DATABASE_URL y una tabla
parametrizable por variable de entorno, sembrando unas pocas filas dummy
solo si la tabla está vacía. Pensado para poder probar el flujo NL->SQL del
agente sin depender de una base de datos real ya existente.

El nombre de la base de datos se toma directamente de DATABASE_URL (no de
una variable separada) para evitar mismatches entre "la DB que se crea" y
"la DB a la que se conecta el agente" (bug real encontrado en una sesión
anterior: nombres de DB con mayúsculas son case-sensitive en la connection
string, y una variable separada podía desincronizarse fácilmente).

Variables de entorno relevantes:
    DATABASE_URL  - connection string completa, incluyendo el nombre final
                    de la base de datos (ej: postgresql://user:pass@localhost:5432/Usuarios)
    APP_DB_TABLE  - nombre de la tabla de demo (default: "Usuario")
"""
import os
import urllib.parse as up

import psycopg2
from psycopg2 import sql


def _parse_connection(database_url: str) -> tuple[dict, str]:
    """Devuelve (conn_params sin dbname, db_name) a partir de DATABASE_URL."""
    parsed = up.urlparse(database_url)
    conn_params = {
        "user": parsed.username,
        "password": parsed.password,
        "host": parsed.hostname,
        "port": parsed.port or 5432,
    }
    db_name = parsed.path.lstrip("/")
    return conn_params, db_name


def _database_exists(conn_params: dict, db_name: str) -> bool:
    with psycopg2.connect(**conn_params, dbname="postgres") as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
            return cur.fetchone() is not None


def _create_database(conn_params: dict, db_name: str) -> None:
    with psycopg2.connect(**conn_params, dbname="postgres") as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
    print(f"[db_bootstrap] Base de datos '{db_name}' creada.")


def _ensure_table_and_seed(conn_params: dict, db_name: str, table_name: str) -> None:
    """Crea la tabla de demo (traducción a Postgres del esquema MySQL original
    `user`: AUTO_INCREMENT -> SERIAL, sin display widths tipo int(11),
    datetime/timestamp de MySQL -> TIMESTAMP de Postgres) y siembra filas
    dummy solo si está vacía.
    """
    with psycopg2.connect(**conn_params, dbname=db_name) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    """
                    CREATE TABLE IF NOT EXISTS {table} (
                        id SERIAL PRIMARY KEY,
                        name VARCHAR(255) NOT NULL DEFAULT '',
                        password VARCHAR(100) NOT NULL DEFAULT '',
                        email VARCHAR(255) NOT NULL DEFAULT '',
                        status_id SMALLINT NOT NULL DEFAULT 0,
                        date_add TIMESTAMP,
                        date_mod TIMESTAMP,
                        date_del TIMESTAMP,
                        CONSTRAINT {uk_email} UNIQUE (email)
                    )
                    """
                ).format(
                    table=sql.Identifier(table_name),
                    uk_email=sql.Identifier(f"uk_{table_name}_email"),
                )
            )
            cur.execute(
                sql.SQL("CREATE INDEX IF NOT EXISTS {idx} ON {table} (date_mod)").format(
                    idx=sql.Identifier(f"idx_{table_name}_date_mod"),
                    table=sql.Identifier(table_name),
                )
            )

            cur.execute(sql.SQL("SELECT COUNT(*) FROM {table}").format(table=sql.Identifier(table_name)))
            count = cur.fetchone()[0]

            if count == 0:
                filas_demo = [
                    ("Ana Gomez", "temp-hash-1", "ana.gomez@example.com", 1),
                    ("Bruno Diaz", "temp-hash-2", "bruno.diaz@example.com", 1),
                    ("Carla Ruiz", "temp-hash-3", "carla.ruiz@example.com", 1),
                    ("Diego Torres", "temp-hash-4", "diego.torres@example.com", 0),
                    ("Elena Vidal", "temp-hash-5", "elena.vidal@example.com", 1),
                ]
                cur.executemany(
                    sql.SQL(
                        "INSERT INTO {table} (name, password, email, status_id, date_add, date_mod) "
                        "VALUES (%s, %s, %s, %s, NOW(), NOW())"
                    ).format(table=sql.Identifier(table_name)),
                    filas_demo,
                )
                print(f"[db_bootstrap] Tabla '{table_name}' sembrada con {len(filas_demo)} filas dummy.")
            else:
                print(f"[db_bootstrap] Tabla '{table_name}' ya tiene {count} filas, no se siembra.")


def ensure_app_database() -> None:
    """Punto de entrada: crea DB/tabla de demo y siembra datos si hace falta.

    Idempotente: se puede llamar en cada arranque sin duplicar nada.
    """
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("[db_bootstrap] DATABASE_URL no está definida, se omite el bootstrap.")
        return

    conn_params, db_name = _parse_connection(database_url)
    if not db_name:
        print("[db_bootstrap] DATABASE_URL no especifica un nombre de base de datos, se omite el bootstrap.")
        return

    table_name = os.getenv("APP_DB_TABLE", "Usuario")

    try:
        if not _database_exists(conn_params, db_name):
            _create_database(conn_params, db_name)
        else:
            print(f"[db_bootstrap] Base de datos '{db_name}' ya existe.")

        _ensure_table_and_seed(conn_params, db_name, table_name)
    except Exception as e:
        print(f"[db_bootstrap] Error en bootstrap de base de datos demo: {e}")
