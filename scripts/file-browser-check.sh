#!/usr/bin/env bash
# Interactive browser check of the file browser.
#
#   ./scripts/file-browser-check.sh [base_url] [uid]
#
# Requires a running backend + frontend and a device with a readable
# /data/local/tmp and an unreadable /data (the usual case on an unrooted
# device). It verifies:
#   * the path box exists and can jump to a deep path whose parents are
#     not readable,
#   * the breadcrumb shows every level and is clickable,
#   * clicking a protected level shows the permission error with the root hint.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
BASE_URL="${1:-http://127.0.0.1:10012}"
UID_ARG="${2:-}"

CHECKER="${ROOT_DIR}/scripts/file-browser-check.mjs"

if [[ ! -f "${CHECKER}" ]]; then
  echo "[error] ${CHECKER} not found" >&2
  exit 1
fi

if [[ -z "${UID_ARG}" ]]; then
  # Take the first online device from the API.
  UID_ARG="$(curl -s "${BASE_URL%/}/../api/v1/devices" 2>/dev/null | python3 -c "
import json,sys
try:
    devices = json.load(sys.stdin).get('data') or []
except Exception:
    devices = []
online = [d for d in devices if d.get('status') == 'online']
print((online or devices)[0]['uid'] if (online or devices) else '')
" 2>/dev/null)"
fi

if [[ -z "${UID_ARG}" ]]; then
  echo "[skip] 没有可用设备，跳过文件浏览交互检查"
  exit 0
fi

echo "device uid = ${UID_ARG}"

# The check asserts the PERMISSION error of a protected directory, so the
# device must not be rooted: a rooted device can read /data and the check
# would (correctly) find no error. Restore the standard state first.
ROOT_DIR_ABS="${ROOT_DIR}"
if [[ -x "${ROOT_DIR_ABS}/.venv/bin/python" ]]; then
  "${ROOT_DIR_ABS}/.venv/bin/python" - <<'PYUNROOT' || true
import json
import sys
import urllib.request

API = "http://127.0.0.1:8010/api/v1"
try:
    with urllib.request.urlopen(f"{API}/devices", timeout=10) as response:
        devices = json.load(response).get("data") or []
except Exception:
    sys.exit(0)

for device in devices:
    if device.get("status") != "online" or not device.get("is_rooted"):
        continue
    try:
        request = urllib.request.Request(
            f"{API}/devices/{device['uid']}/unroot", method="POST", data=b""
        )
        urllib.request.urlopen(request, timeout=20).read()
        print(f"unrooted {device['uid']} for the permission check")
    except Exception as exc:
        print(f"could not unroot {device['uid']}: {exc}")
PYUNROOT
fi

node "${CHECKER}" "${BASE_URL}" "${UID_ARG}"
