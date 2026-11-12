#!/usr/bin/env bash
# Start both services in the background and wait until they answer.
#
#   ./scripts/start.sh          # backend :8010 + frontend :10012 (0.0.0.0)
#   ./scripts/start.sh stop     # stop both
#
# Logs go to <project>/logs/.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
LOG_DIR="${ROOT_DIR}/logs"
VENV_PY="${ROOT_DIR}/.venv/bin/python"
BACKEND_PORT="${SERVER_PORT:-8010}"
FRONTEND_PORT="${FRONTEND_PORT:-10012}"
BIND_HOST="${BIND_HOST:-0.0.0.0}"

mkdir -p "${LOG_DIR}"

stop_services() {
  pkill -f "app.main" 2>/dev/null || true
  pkill -f "vite --port ${FRONTEND_PORT}" 2>/dev/null || true
  sleep 1
  echo "[stop] 服务已停止"
}

case "${1:-start}" in
  stop)
    stop_services
    exit 0
    ;;
  start) ;;
  *)
    echo "usage: $0 [start|stop]" >&2
    exit 2
    ;;
esac

if ! bash "${ROOT_DIR}/scripts/check-env.sh"; then
  exit 1
fi

stop_services

# --- backend -------------------------------------------------------------
(
  cd "${ROOT_DIR}/backend"
  SERVER_HOST="${BIND_HOST}" SERVER_PORT="${BACKEND_PORT}" \
    nohup "${VENV_PY}" -m app.main > "${LOG_DIR}/backend.log" 2>&1 &
  echo $! > "${LOG_DIR}/backend.pid"
)

# --- frontend ------------------------------------------------------------
(
  cd "${ROOT_DIR}/frontend"
  export NVM_DIR="${HOME}/.nvm"
  # shellcheck disable=SC1091
  [[ -s "${NVM_DIR}/nvm.sh" ]] && source "${NVM_DIR}/nvm.sh" && nvm use 22 >/dev/null 2>&1
  nohup npm run dev -- --port "${FRONTEND_PORT}" --host "${BIND_HOST}" \
    > "${LOG_DIR}/frontend.log" 2>&1 &
  echo $! > "${LOG_DIR}/frontend.pid"
)

wait_for_port() {
  local port="$1" tries=60
  while (( tries-- > 0 )); do
    if (exec 3<>"/dev/tcp/127.0.0.1/${port}") 2>/dev/null; then
      exec 3>&- 2>/dev/null || true
      return 0
    fi
    sleep 0.5
  done
  return 1
}

if wait_for_port "${BACKEND_PORT}"; then
  echo "[backend] http://${BIND_HOST}:${BACKEND_PORT}  (pid $(cat "${LOG_DIR}/backend.pid"))"
else
  echo "[error] 后端未能在 ${BACKEND_PORT} 端口启动，日志：" >&2
  tail -30 "${LOG_DIR}/backend.log" >&2 || true
  exit 1
fi

if wait_for_port "${FRONTEND_PORT}"; then
  echo "[frontend] http://${BIND_HOST}:${FRONTEND_PORT}  (pid $(cat "${LOG_DIR}/frontend.pid"))"
else
  echo "[error] 前端未能在 ${FRONTEND_PORT} 端口启动，日志：" >&2
  tail -30 "${LOG_DIR}/frontend.log" >&2 || true
  exit 1
fi

echo "[logs] ${LOG_DIR}"
