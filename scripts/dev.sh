#!/usr/bin/env bash
# Start the backend API and the frontend dev server.
#
#   ./scripts/dev.sh              # backend :8010 + frontend :10012
#   ./scripts/dev.sh backend      # only the backend
#   ./scripts/dev.sh frontend     # only the frontend
#
# Both services bind 0.0.0.0 so they are reachable from other machines.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
VENV_PY="${ROOT_DIR}/.venv/bin/python"
BACKEND_PORT="${SERVER_PORT:-8010}"
FRONTEND_PORT="${FRONTEND_PORT:-10012}"
BIND_HOST="${BIND_HOST:-0.0.0.0}"

start_backend() {
  if [[ ! -x "${VENV_PY}" ]]; then
    echo "[error] virtualenv missing: ${VENV_PY}" >&2
    echo "        run: python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt" >&2
    exit 1
  fi
  cd "${ROOT_DIR}/backend"
  echo "[backend] http://${BIND_HOST}:${BACKEND_PORT}  (docs: /docs)"
  SERVER_HOST="${BIND_HOST}" SERVER_PORT="${BACKEND_PORT}" exec "${VENV_PY}" -m app.main
}

start_frontend() {
  cd "${ROOT_DIR}/frontend"
  if [[ ! -d node_modules ]]; then
    echo "[frontend] installing dependencies..."
    npm install
  fi
  echo "[frontend] http://${BIND_HOST}:${FRONTEND_PORT}"
  exec npm run dev -- --port "${FRONTEND_PORT}" --host "${BIND_HOST}"
}

case "${1:-all}" in
  backend) start_backend ;;
  frontend) start_frontend ;;
  all)
    start_backend &
    BACKEND_PID=$!
    trap 'kill ${BACKEND_PID} 2>/dev/null || true' EXIT
    sleep 2
    start_frontend
    ;;
  *)
    echo "usage: $0 [all|backend|frontend]" >&2
    exit 2
    ;;
esac
