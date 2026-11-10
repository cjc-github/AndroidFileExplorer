#!/usr/bin/env bash
# Full verification gate.
#
#   ./scripts/verify.sh
#
# Runs everything that must be green before a delivery:
#   1. backend unit + integration tests
#   2. backend end to end (TestClient)
#   3. backend end to end (real uvicorn + real HTTP)
#   4. frontend type check + production build
#   5. frontend render check in a real headless browser (every page)
#   6. file browser interaction check (path box, breadcrumb, permission error)
#
# NOTE: step 5 is NOT optional. A successful `vite build` does not prove the
# page renders - a react/react-dom version mismatch builds fine and produces a
# blank page at runtime.
#
# Step 3 stops the backend it started, so the services are restarted before
# step 5 to make sure the browser check talks to a live API.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
VENV_PY="${ROOT_DIR}/.venv/bin/python"
FAILED=0

# The frontend needs node >= 20; the default node on some hosts is too old.
export NVM_DIR="${HOME}/.nvm"
if [[ -s "${NVM_DIR}/nvm.sh" ]]; then
  # shellcheck disable=SC1091
  source "${NVM_DIR}/nvm.sh" >/dev/null 2>&1
  nvm use 22 >/dev/null 2>&1 || nvm use lts >/dev/null 2>&1 || true
fi
echo "node: $(node -v 2>/dev/null || echo 'not found')"

step() {
  echo
  echo "############################################################"
  echo "# $1"
  echo "############################################################"
}

run() {
  local label="$1"
  shift
  if "$@"; then
    echo "[PASS] ${label}"
  else
    echo "[FAIL] ${label}"
    FAILED=1
  fi
}

step "1/6 backend tests"
run "pytest" bash -c "cd '${ROOT_DIR}/backend' && '${VENV_PY}' -m pytest -q"

step "2/6 backend end to end (TestClient)"
run "e2e_demo" bash -c "cd '${ROOT_DIR}/backend' && '${VENV_PY}' scripts/e2e_demo.py"

step "3/6 backend end to end (real uvicorn + real HTTP)"
run "live_server_check" bash -c "cd '${ROOT_DIR}/backend' && '${VENV_PY}' scripts/live_server_check.py"

step "4/6 frontend build"
run "frontend build" bash -c "cd '${ROOT_DIR}/frontend' && npm run build"

step "5/6 frontend render check (real headless browser, every page)"
# Make sure both services are up: step 3 stopped the backend it started.
run "start services" bash -c "cd '${ROOT_DIR}' && ./scripts/start.sh"
sleep 3
run "render-check" bash -c "cd '${ROOT_DIR}' && ./scripts/render-check.sh"

step "6/6 file browser interaction check"
run "file-browser-check" bash -c "cd '${ROOT_DIR}' && ./scripts/file-browser-check.sh"

echo
echo "############################################################"
if [[ "${FAILED}" -eq 0 ]]; then
  echo "# 全部验证通过"
else
  echo "# 存在失败项"
fi
echo "############################################################"
exit "${FAILED}"
