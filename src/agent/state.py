from typing import Annotated, Dict, List, Optional, Union
from typing_extensions import TypedDict, NotRequired

from langgraph.graph.message import add_messages


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
