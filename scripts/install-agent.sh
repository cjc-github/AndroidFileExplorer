#!/usr/bin/env bash
# Make a remote machine usable by the platform - without any other component.
#
#   ./scripts/install-agent.sh <host> [port] [agent_port]
#
# It copies the agent from this project to the remote machine, starts it and
# verifies that the platform can discover the devices through it.
#
# The remote machine only needs python3 and adb - nothing from this project
# and nothing from any other repository.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
HOST="${1:-}"
AGENT_PORT="${2:-12345}"
REMOTE_AGENT_PORT="${3:-18077}"
REMOTE_DIR="~/afe-agent"

if [[ -z "${HOST}" ]]; then
  echo "用法: $0 <远程机器IP> [agent端口] [远程agent监听端口]" >&2
  echo "  例: $0 192.168.114.130 12345 18077" >&2
  exit 2
fi

PYTHON="${ROOT_DIR}/.venv/bin/python"
if [[ ! -x "${PYTHON}" ]]; then
  echo "[error] 找不到虚拟环境: ${PYTHON}" >&2
  echo "        python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt" >&2
  exit 1
fi

echo "=== 1/3 停止远端旧 agent ==="
"${PYTHON}" "${ROOT_DIR}/backend/scripts/stop_agent.py" \
  --host "${HOST}" --port "${AGENT_PORT}" 2>&1 | grep -E "PORT_FREE|KILLED" | tail -2

echo
echo "=== 2/3 部署并启动 agent ==="
"${PYTHON}" "${ROOT_DIR}/backend/scripts/deploy_agent.py" \
  --host "${HOST}" --port "${AGENT_PORT}" \
  --remote-dir "${REMOTE_DIR}" --remote-port "${REMOTE_AGENT_PORT}" --start 2>&1 | tail -8

echo
echo "=== 3/3 在平台登记这台机器 ==="
API="${API_BASE:-http://127.0.0.1:8010/api/v1}"
curl -s -X POST "${API}/engines" \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"agent ${HOST}:${REMOTE_AGENT_PORT}\",\"host\":\"${HOST}\",\"port\":${REMOTE_AGENT_PORT},\"kind\":\"remote\"}" \
  | python3 -c "
import json, sys
result = json.load(sys.stdin)
print('  ', result.get('message'))
data = result.get('data') or {}
if data:
    print(f\"   engine_id={data.get('id')} devices={data.get('device_count')}\")
" 2>/dev/null || echo "  (登记失败：请确认平台后端已启动)"

echo
echo "=== 完成 ==="
echo "远程机器 ${HOST} 现在只依赖本项目的 agent（python3 + adb），不再需要任何外部组件。"
