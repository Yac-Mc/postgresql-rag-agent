FROM python:3.12.9-slim

WORKDIR /app

# Dependencias de sistema minimas que psycopg2-binary/otras libs suelen necesitar
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# torch desde el indice CPU-only oficial: Cloud Run no tiene GPU, y la wheel
# default de PyPI arrastra ~2GB+ de paquetes nvidia_cuda_* innecesarios que
# alargan mucho el build (y pueden superar el timeout de Cloud Build).
RUN pip install --no-cache-dir torch==2.7.1 --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Instala el propio paquete (layout src/) para que "agent" sea importable como
# top-level (mismo mecanismo que "pip install -e ." usa en desarrollo local).
RUN pip install --no-cache-dir --no-deps -e .

ENV PYTHONUNBUFFERED=1

# Deshabilita el backend "xet" de HuggingFace Hub: su cliente Rust no confia
# en la CA del proxy de red corporativo y falla al descargar modelos (SentenceTransformer).
ENV HF_HUB_DISABLE_XET=1

# Cloud Run inyecta $PORT en runtime (default 8080, no 10000 como Render)
CMD exec uvicorn src.agent.api:app --host 0.0.0.0 --port ${PORT:-8080}
