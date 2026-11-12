#!/usr/bin/env bash
# Check the local environment before starting or deploying the platform.
#
#   ./scripts/check-env.sh                 # backend + frontend
#   ./scripts/check-env.sh --backend-only  # only the API server
#
# Required: venv + backend deps (additionally node/npm for the frontend).
# adb is reported as a warning because a remote-only deployment does not
# need a local adb.  SSH direct mode only needs the Python package asyncssh.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
VENV_PY="${ROOT_DIR}/.venv/bin/python"
BACKEND_ONLY=0
FAILED=0
WARNED=0

for arg in "$@"; do
  case "${arg}" in
    --backend-only) BACKEND_ONLY=1 ;;
    -h|--help)
      echo "用法: $0 [--backend-only]"
      exit 0
      ;;
    *)
      echo "未知参数: ${arg}" >&2
      exit 2
      ;;
  esac
done

# The frontend needs a modern node; use the same nvm setup as start.sh.
export NVM_DIR="${HOME}/.nvm"
# shellcheck disable=SC1091
[[ -s "${NVM_DIR}/nvm.sh" ]] && source "${NVM_DIR}/nvm.sh" && nvm use 22 >/dev/null 2>&1 || true

ok()   { echo "[ok]   $*"; }
warn() { echo "[warn] $*"; WARNED=1; }
fail() { echo "[fail] $*"; FAILED=1; }

echo "=== 本地环境检查 ==="

if [[ -x "${VENV_PY}" ]]; then
  PY_VERSION="$("${VENV_PY}" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))' 2>/dev/null || echo unknown)"
  if "${VENV_PY}" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
  then
    ok "Python ${PY_VERSION}"
  else
    fail "Python ${PY_VERSION}，需要 3.10+：python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt"
  fi
else
  fail "缺少虚拟环境：python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt"
fi

if [[ -x "${VENV_PY}" ]]; then
  if "${VENV_PY}" - <<'PY' >/dev/null 2>&1
import fastapi, uvicorn, sqlmodel, aiosqlite, dotenv, loguru
PY
  then
    ok "后端 Python 依赖完整"
  else
    fail "后端依赖缺失：.venv/bin/pip install -r backend/requirements.txt"
  fi

  if "${VENV_PY}" -c "import asyncssh" >/dev/null 2>&1; then
    ok "SSH 直连依赖 asyncssh 可用"
  else
    warn "未安装 asyncssh：SSH 直连不可用；执行 .venv/bin/pip install asyncssh"
  fi
fi

ADB_BIN="${ADB_PATH:-adb}"
if command -v "${ADB_BIN}" >/dev/null 2>&1; then
  ADB_VERSION="$(timeout 5 "${ADB_BIN}" version 2>/dev/null | head -1 || true)"
  if [[ -n "${ADB_VERSION}" ]]; then
    ok "本机 adb 可用：${ADB_VERSION}"
  else
    warn "adb 存在但无法执行 version：检查 ${ADB_BIN}"
  fi
else
  warn "未找到 adb（ADB_PATH=${ADB_BIN}）：本机设备发现不可用；仅使用远程机器时可忽略"
fi

if [[ "${BACKEND_ONLY}" -eq 0 ]]; then
  if command -v node >/dev/null 2>&1; then
    NODE_VERSION="$(node -v 2>/dev/null || true)"
    NODE_MAJOR="${NODE_VERSION#v}"
    NODE_MAJOR="${NODE_MAJOR%%.*}"
    if [[ "${NODE_MAJOR}" =~ ^[0-9]+$ ]] && (( NODE_MAJOR >= 20 )); then
      ok "Node ${NODE_VERSION}"
    else
      fail "Node ${NODE_VERSION:-未知}，需要 20 或更高版本"
    fi
  else
    fail "未找到 node：安装 Node.js 20+（或使用 nvm use 22）"
  fi

  if command -v npm >/dev/null 2>&1; then
    ok "npm $(npm -v 2>/dev/null || echo unknown)"
  else
    fail "未找到 npm：安装 Node.js 20+"
  fi

  if [[ -d "${ROOT_DIR}/frontend/node_modules" ]]; then
    ok "前端依赖已安装"
  else
    warn "缺少 frontend/node_modules：cd frontend && npm install"
  fi
fi

echo
if [[ "${FAILED}" -ne 0 ]]; then
  echo "环境检查未通过：请先修复上面的 [fail] 项。"
  exit 1
fi
if [[ "${WARNED}" -ne 0 ]]; then
  echo "环境检查通过（存在警告项；对应能力可能不可用）。"
else
  echo "环境检查全部通过。"
fi
