#!/usr/bin/env bash
# Launch the Sheffield S106 Auditor API on the NVIDIA DGX Spark.
#
# Usage (from the repo root on the Spark):
#   ./scripts/run_spark.sh
#
# Recommended: run it inside tmux so it survives the SSH session —
#   tmux new-session -d -s s106-api /path/to/repo/scripts/run_spark.sh
#   ssh -t spark3 tmux attach -t s106-api      # Ctrl-B then D to detach
#
# Every variable can be overridden from the environment before calling this.
set -euo pipefail
cd "$(dirname "$0")/.."

export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://spark:sparkpassword@localhost:5432/sheffield_s106}"

# On the Spark itself Ollama listens on :11434. The .env.example default of :11435
# is the organiser's laptop-side SSH-tunnel port, which is wrong when running
# server-side, so it is overridden here.
export VLLM_BASE_URL="${VLLM_BASE_URL:-http://127.0.0.1:11434/v1}"
export VLLM_API_KEY="${VLLM_API_KEY:-ollama}"
export VLLM_MODEL_NAME="${VLLM_MODEL_NAME:-nemotron-3.5-lightning:30b}"

# Organiser Spark limits: 8,192 tokens per request, one model loaded at a time.
export LLM_NUM_CTX="${LLM_NUM_CTX:-8192}"
export LLM_NUM_PREDICT="${LLM_NUM_PREDICT:-2048}"
export LLM_KEEP_ALIVE="${LLM_KEEP_ALIVE:-0}"

# Bound on 0.0.0.0 so the dashboard is reachable from a laptop browser
# (http://spark3.local:8080) without an SSH tunnel.
exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port "${APP_PORT:-8080}"
