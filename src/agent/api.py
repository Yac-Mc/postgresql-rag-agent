from fastapi import FastAPI, HTTPException, Security
from fastapi.security import APIKeyHeader
import os
import uvicorn
from pydantic import BaseModel
from langchain_core.messages import HumanMessage
from dotenv import load_dotenv
import logging

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

API_KEY = os.getenv("API_KEY")
if not API_KEY:
    raise RuntimeError("API_KEY no esta definida en el entorno (.env)")

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def verify_api_key(api_key: str = Security(api_key_header)) -> None:
    if api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

# Import a nivel de módulo (fuera de cualquier función async): setup_graph()
# en graph.py crea su propio event loop con asyncio.new_event_loop() +
# run_until_complete() a nivel de módulo. Si este import se hiciera dentro de
# startup_event (que ya corre sobre el loop de uvicorn), fallaría con
# "Cannot run the event loop while another loop is running". Importándolo acá,
# antes de que uvicorn arranque su propio loop, setup_graph() corre sin problema.
from agent.graph import get_graph

app = FastAPI(title="Chatbot SQL API")


class Pregunta(BaseModel):
    texto: str


@app.on_event("startup")
async def startup_event():
    app.state.graph = await get_graph()


@app.post("/chat", dependencies=[Security(verify_api_key)])
async def responder(pregunta: Pregunta):
    result = None
    try:
        result = await app.state.graph.ainvoke({
            "messages": [HumanMessage(content=pregunta.texto)],
            "pregunta": pregunta.texto,
            "errores": [],
            "intentos_ejecucion": 0,
            "intentos_generacion_sql": 0,
        })

        if result.get("errores"):
            error_tipo = result.get("metadata", {}).get("error_tipo")
            if error_tipo == "error_transitorio":
                raise HTTPException(status_code=503, detail=result["errores"])
            raise HTTPException(status_code=400, detail=result["errores"])

        return {
            "respuesta": result["respuesta_natural"],
            "metadata": result.get("metadata", {})
        }
    except HTTPException:
        raise
    except Exception as e:
        # Log del error para debugging
        logger.error(f"Error inesperado: {str(e)}")
        # Mensaje amigable al usuario
        raise HTTPException(
            status_code=500,
            detail="No fue posible el manejo de tu solicitud, por favor reintenta de otra forma."
        )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
