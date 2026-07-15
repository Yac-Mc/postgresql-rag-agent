FROM python:3.12.9-slim

WORKDIR /app

# Dependencias de sistema minimas que psycopg2-binary/otras libs suelen necesitar
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1

# Cloud Run inyecta $PORT en runtime (default 8080, no 10000 como Render)
CMD exec uvicorn src.agent.api:app --host 0.0.0.0 --port ${PORT:-8080}
