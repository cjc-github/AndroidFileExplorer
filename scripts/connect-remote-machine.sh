#!/usr/bin/env bash
# Register a remote machine in the platform and verify the whole chain.
#
#   ./scripts/connect-remote-machine.sh <host> <port> [name]
#
# It registers the machine through the API, probes it, and - when the machine
# exposes no device - prints the exact commands to start one on that host.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
API="${API_BASE:-http://127.0.0.1:8010/api/v1}"

HOST="${1:-}"
PORT="${2:-12345}"
NAME="${3:-}"

if [[ -z "${HOST}" ]]; then
  echo "usage: $0 <host> [port] [name]" >&2
  echo "  host  远程机器 IP，例如 192.168.114.130" >&2
  echo "  port  agent_server 端口，默认 12345" >&2
  exit 2
fi
[[ -z "${NAME}" ]] && NAME="远程机器 ${HOST}"

echo "=== 1. 网络连通性 ==="
if timeout 5 bash -c "</dev/tcp/${HOST}/${PORT}" 2>/dev/null; then
  echo "  OK  ${HOST}:${PORT} 可达"
else
  echo "  ERR ${HOST}:${PORT} 不可达 —— 检查目标机是否开机、agent_server 是否启动"
  exit 1
fi

echo
echo "=== 2. 登记机器 ==="
RESPONSE="$(curl -s -X POST "${API}/engines" \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"${NAME}\",\"host\":\"${HOST}\",\"port\":${PORT},\"kind\":\"remote\"}")"
echo "  ${RESPONSE}"

ENGINE_ID="$(printf '%s' "${RESPONSE}" | python3 -c "
import json,sys
try:
    print(json.load(sys.stdin)['data']['id'])
except Exception:
    print('')
")"

if [[ -z "${ENGINE_ID}" ]]; then
  echo "  ERR 登记失败"
  exit 1
fi

echo
echo "=== 3. 测试连接 ==="
curl -s -X POST "${API}/engines/${ENGINE_ID}/test" | python3 -m json.tool 2>/dev/null | head -12

echo
echo "=== 4. 同步设备 ==="
curl -s "${API}/devices?refresh=true" | python3 -c "
import json,sys
d=json.load(sys.stdin).get('data') or []
print(f'  设备数: {len(d)}')
for x in d:
    print(f\"    uid={x['uid']} serial={x['serial']} status={x['status']} engine_id={x['engine_id']}\")
"

echo
echo "=== 5. 若该机器没有设备，在它上面启动一个模拟器 ==="
cat <<EOF
在 ${HOST} 上执行（该机需要 /dev/kvm 与 Android SDK）：

  ls ~/Android/Sdk/emulator/emulator
  # 有 AVD 时：
  cd ~/Android/Sdk/emulator && nohup ./emulator -avd <AVD名> \\
      -no-window -no-audio -no-boot-anim -gpu swiftshader_indirect > /tmp/emu.log 2>&1 &

  # 等模拟器启动完成（boot_completed=1）后再点「同步」：
  adb devices
  adb -s emulator-5554 shell getprop sys.boot_completed

没有 AVD 时：先在 Android Studio 里创建一个，或用 USB 连真机后 `adb devices`。
EOF

echo
echo "=== 完成 ==="
echo "前端：${FRONTEND_URL:-http://127.0.0.1:10012/}"
echo "  远程机器连接配置 -> /machines"
echo "  设备资源管理     -> /devices"
