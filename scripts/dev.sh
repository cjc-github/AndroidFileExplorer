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

# Keep the actual frontend process on the same Node version that check-env
# validated (nvm is a shell function, so it must be sourced here too).
export NVM_DIR="${HOME}/.nvm"
# shellcheck disable=SC1091
[[ -s "${NVM_DIR}/nvm.sh" ]] && source "${NVM_DIR}/nvm.sh" && nvm use 22 >/dev/null 2>&1 || true

start_backend() {
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

if [[ "${1:-all}" == "backend" ]]; then
  bash "${ROOT_DIR}/scripts/check-env.sh" --backend-only || exit 1
else
  bash "${ROOT_DIR}/scripts/check-env.sh" || exit 1
fi

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
