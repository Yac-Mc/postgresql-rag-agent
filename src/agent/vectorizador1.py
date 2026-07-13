from sqlalchemy import create_engine, inspect
from langchain.schema import Document
import pandas as pd
import chromadb
from sentence_transformers import SentenceTransformer
import csv
import os
import logging
import gc
import time
import psutil

NUEVO_CHROMA_PATH = "chroma_db_v2"

def limpiar_metadata(metadata_dict):
    return {
        k: str(v) if not isinstance(v, (str, int, float, bool)) else v
        for k, v in metadata_dict.items()
    }

def dividir_en_lotes(lista, tam_lote):
    for i in range(0, len(lista), tam_lote):
        yield lista[i:i + tam_lote]

def registrar_error(tabla, mensaje, error_log_path):
    try:
        with open(error_log_path, mode='a', newline='', encoding='utf-8') as error_log:
            error_writer = csv.writer(error_log)
            error_writer.writerow([tabla, mensaje])
    except Exception as e:
        logger.error(f"No se pudo escribir en log de errores: {e}")
    logger.error(f"Error en tabla {tabla}: {mensaje}")

def es_columna_textual(col):
    return any(tipo in str(col['type']).lower() for tipo in ['text', 'varchar', 'char'])

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

DB_URL = "postgresql://postgres.dsxlxkbwsrjqqexfvqzj:Paloma2695147-@aws-0-eu-west-3.pooler.supabase.com:5432/postgres"
engine = create_engine(DB_URL)

inspector = inspect(engine)

tablas_a_procesar = ["instrucciones_sql"]

logger.info(f"Tablas a procesar: {tablas_a_procesar}")

error_log_file = "errores_lectura_v2.csv"
with open(error_log_file, mode='w', newline='', encoding='utf-8') as f:
    writer = csv.writer(f)
    writer.writerow(["Tabla", "Error"])

CHUNK_SIZE = 20000
BATCH_SIZE = 500
EMBEDDING_BATCH_SIZE = 50

chroma_client = chromadb.PersistentClient(path=NUEVO_CHROMA_PATH)

modelo_embeddings = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
coleccion = chroma_client.get_or_create_collection(name="mi_base_vectorizada")

log_file = "log_inserciones_v2.csv"
with open(log_file, mode='w', newline='', encoding='utf-8') as log:
    writer = csv.writer(log)
    writer.writerow(["Tabla", "Chunk", "Lote", "Documentos", "Memoria (MB)", "Tiempo"])

def escribir_log(tabla, chunk, lote, documentos, memoria, tiempo):
    try:
        with open(log_file, mode='a', newline='', encoding='utf-8') as log:
            writer = csv.writer(log)
            writer.writerow([tabla, chunk, lote, documentos, memoria, tiempo])
    except Exception as e:
        logger.error(f"No se pudo escribir en log: {e}")

for tabla in tablas_a_procesar:
    logger.info(f"\n{'='*60}")
    logger.info(f"PROCESANDO TABLA: {tabla}")
    logger.info(f"{'='*60}")
    
    try:
        columnas = inspector.get_columns(tabla, schema="public")
        columnas_texto = [col['name'] for col in columnas if es_columna_textual(col)]

        if not columnas_texto:
            registrar_error(tabla, "No se encontraron columnas textuales", error_log_file)
            logger.info("Saltando tabla - sin columnas textuales")
            continue

        columna_id = inspector.get_pk_constraint(tabla, schema="public")["constrained_columns"]
        columna_id = columna_id[0] if columna_id else None

        if not columna_id:
            registrar_error(tabla, "No tiene columna primaria", error_log_file)
            continue

        logger.info(f"Columnas textuales: {columnas_texto}")
        logger.info(f"Columna ID: {columna_id}")

        count_query = f"SELECT COUNT(*) FROM public.{tabla}"
        total_registros = pd.read_sql_query(count_query, engine).iloc[0, 0]
        logger.info(f"Total de registros: {total_registros:,}")

        offset = 0
        documentos_totales = 0
        chunk_counter = 0
        batch_counter = 0

        while offset < total_registros:
            chunk_counter += 1
            chunk_start_time = time.time()
            logger.info(f"\n--- Chunk {chunk_counter}: offset {offset:,} - {offset + CHUNK_SIZE:,} ---")
            
            query = f"""
                SELECT {columna_id}, {', '.join(columnas_texto)} 
                FROM public.{tabla} 
                ORDER BY {columna_id}
                LIMIT {CHUNK_SIZE} OFFSET {offset}
            """
            
            df_chunk = pd.read_sql_query(query, engine)
            
            documentos_chunk = []
            registros_procesados = 0
            
            for _, fila in df_chunk.iterrows():
                valores_texto = []
                for col in columnas_texto:
                    if pd.notnull(fila[col]) and str(fila[col]).strip():
                        valores_texto.append(f"{col}: {fila[col]}")
                
                if valores_texto:
                    contenido = f"Tabla: {tabla}\n" + "\n".join(valores_texto)
                    metadata = limpiar_metadata({
                        "tabla": tabla,
                        "id": fila[columna_id],
                        "columnas_vectorizadas": ",".join(columnas_texto)
                    })
                    documentos_chunk.append(Document(page_content=contenido, metadata=metadata))
            
            logger.info(f"Registros procesados: {len(documentos_chunk)}/{CHUNK_SIZE}")
            
            if documentos_chunk:
                logger.info(f"Vectorizando {len(documentos_chunk)} documentos...")
                
                all_embeddings = []
                for i in range(0, len(documentos_chunk), EMBEDDING_BATCH_SIZE):
                    batch_start = i
                    batch_end = min(i + EMBEDDING_BATCH_SIZE, len(documentos_chunk))
                    batch_docs = documentos_chunk[batch_start:batch_end]
                    
                    batch_embeddings = modelo_embeddings.encode(
                        [doc.page_content for doc in batch_docs],
                        convert_to_numpy=True,
                        show_progress_bar=False,
                        batch_size=EMBEDDING_BATCH_SIZE
                    )
                    all_embeddings.extend(batch_embeddings.tolist())
                    
                    del batch_docs, batch_embeddings
                    gc.collect()
                
                for i, (batch_docs, batch_embeddings) in enumerate(zip(
                    dividir_en_lotes(documentos_chunk, BATCH_SIZE),
                    dividir_en_lotes(all_embeddings, BATCH_SIZE)
                )):
                    try:
                        coleccion.add(
                            documents=[doc.page_content for doc in batch_docs],
                            embeddings=batch_embeddings,
                            metadatas=[doc.metadata for doc in batch_docs],
                            ids=[f"{tabla}_{doc.metadata['id']}_{offset + i * BATCH_SIZE}" for doc in batch_docs]
                        )
                        
                        batch_counter += 1
                        documentos_totales += len(batch_docs)
                        
                        memory_usage = psutil.Process().memory_info().rss / 1024 / 1024
                        chunk_time = time.time() - chunk_start_time
                        
                        logger.info(f"  Lote {batch_counter}: {len(batch_docs)} docs | "
                                  f"Memoria: {memory_usage:.1f}MB | "
                                  f"Tiempo: {chunk_time:.1f}s")
                        
                        escribir_log(tabla, chunk_counter, batch_counter, 
                                   len(batch_docs), f"{memory_usage:.1f}", f"{chunk_time:.1f}")
                        
                        del batch_docs, batch_embeddings
                        gc.collect()
                        
                    except Exception as e:
                        error_msg = f"Error insertando lote: {str(e)}"
                        logger.error(error_msg)
                        escribir_log(tabla, chunk_counter, batch_counter, 0, "N/A", f"Error: {error_msg}")
            
            offset += CHUNK_SIZE
            del df_chunk, documentos_chunk, all_embeddings
            gc.collect()
            
            logger.info(f"Progreso: {min(offset, total_registros):,}/{total_registros:,} registros")
            logger.info(f"Tiempo del chunk: {time.time() - chunk_start_time:.1f}s")
            
            time.sleep(1)
            
        logger.info(f"Tabla {tabla} completada: {documentos_totales:,} documentos procesados")
        
    except Exception as e:
        error_msg = f"Error procesando tabla {tabla}: {str(e)}"
        logger.error(error_msg)
        registrar_error(tabla, error_msg, error_log_file)
        escribir_log(tabla, 0, 0, 0, "N/A", f"Error: {error_msg}")

logger.info(f"\n{'='*60}")
logger.info("PROCESAMIENTO COMPLETADO")
logger.info(f"Nueva base vectorial creada en: {os.path.abspath(NUEVO_CHROMA_PATH)}")
logger.info(f"Log de inserciones: {os.path.abspath(log_file)}")
logger.info(f"Log de errores: {os.path.abspath(error_log_file)}")
logger.info(f"{'='*60}")

print(f"VERIFICACION FINAL:")
print(f"ChromaDB ubicación: {NUEVO_CHROMA_PATH}")
print(f"Documentos en colección: {coleccion.count}")
print(f"Colección: {coleccion.name}")
