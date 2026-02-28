#!/bin/bash
set -e

set -o allexport
set +o allexport

echo "🚀 Starting FastAPI app for the Custom Chatbot service..."

exec gunicorn environment.server:app \
  -k uvicorn.workers.UvicornWorker \
  --bind "${APP_HOST:-0.0.0.0}:${APP_PORT:-8000}" \
  --workers "${APP_WORKERS:-2}" \
  --timeout "${GUNICORN_TIMEOUT:-500}"
