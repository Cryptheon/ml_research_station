#!/usr/bin/env bash
# Start the ResearchStation API server.
# Usage: ./scripts/serve.sh [--reload] [--port 8080]
set -euo pipefail

PORT="${PORT:-8080}"
RELOAD_FLAG=""

for arg in "$@"; do
  case $arg in
    --reload) RELOAD_FLAG="--reload" ;;
    --port)   shift; PORT="$1" ;;
  esac
done

echo "Starting ResearchStation API on http://localhost:${PORT}"
uvicorn research_station.api.app:app \
  --host 0.0.0.0 \
  --port "${PORT}" \
  --log-level info \
  ${RELOAD_FLAG}
