# Dockerfile for Sheffield Section 106 Autonomous Auditor
# Target: NVIDIA DGX Spark (GB10, linux/arm64) — all images must be ARM64.
#
# Build: docker build -t s106-auditor .
# Run:  docker run -p 8080:8080 -e VLLM_BASE_URL=http://spark3.local:11434/v1 s106-auditor
# Or via docker-compose (see docker-compose.yml for the db service)

FROM python:3.11-slim

# ---- ARM64 metadata ----
# This image is intended for linux/arm64 execution on NVIDIA DGX Spark.
# On x86 hosts,colinux/arm64 base images will be auto-selected by Docker.

# ---- Environment ----
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    VLLM_BASE_URL=http://localhost:11434/v1 \
    VLLM_API_KEY=ollama \
    VLLM_MODEL_NAME=qwen3.6:35b-a3b \
    APP_HOST=0.0.0.0 \
    APP_PORT=8080

# ---- Work directory ----
WORKDIR /app

# ---- Install dependencies first (layer caching) ----
# Copy only requirements first to leverage Docker layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---- Copy application code ----
COPY . .

# ---- Expose FastAPI port ----
EXPOSE 8080

# ---- Health check ----
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s \
  CMD curl -f http://localhost:8080/api/health || exit 1

# ---- Start the server ----
# Use production-ready gunicorn+uvicorn workers when deploying
# For development: uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
CMD uvicorn app.main:app --host "$APP_HOST" --port "$APP_PORT"