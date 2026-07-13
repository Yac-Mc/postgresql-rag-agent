from fastapi import FastAPI, HTTPException
import uvicorn
from pydantic import BaseModel
from langchain_core.messages import HumanMessage
from dotenv import load_dotenv
import logging

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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


@app.post("/chat")
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
    uvicorn.run(app, host="0.0.0.0", port=8000)
