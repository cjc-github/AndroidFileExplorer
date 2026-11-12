#!/usr/bin/env bash
# Render the frontend in a real headless Chrome and assert the DOM.
#
#   ./scripts/render-check.sh [url] [--page machines|devices|files|picker] [--expect-device]
#
# Without a URL every known page is checked:
#   /machines                      远程机器连接配置
#   /devices                       设备资源管理
#   /files                         文件浏览（设备选择）
#   /devices/$uid/files            文件浏览（某台设备）
#
# Uses `google-chrome --headless --dump-dom`, so no npm dependency is needed.
#
# NOTE: a successful `vite build` does NOT prove the page renders. This check
# executes the app in a browser and asserts the mounted DOM, which is the
# only way to catch runtime failures (e.g. mismatched react/react-dom).
set -uo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:10012}"
ONLY_PAGE=""
EXPECT_DEVICE="no"
TARGET_URL=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --page) ONLY_PAGE="$2"; shift 2 ;;
    --expect-device) EXPECT_DEVICE="yes"; shift ;;
    -*) echo "unknown argument: $1" >&2; exit 2 ;;
    *) TARGET_URL="$1"; shift ;;
  esac
done

CHROME="$(command -v google-chrome || command -v chromium || command -v google-chrome-stable)"
if [[ -z "${CHROME}" ]]; then
  echo "[error] no chrome/chromium found on PATH" >&2
  exit 1
fi

FAILED=0

# check <file> <label> <regex>
check() {
  local file="$1" label="$2" pattern="$3"
  if grep -qE "${pattern}" "${file}"; then
    echo "PASS  ${label}"
  else
    echo "FAIL  ${label}   (pattern: ${pattern})"
    FAILED=1
  fi
}

# device_uid: the uid of an online device, empty when there is none
device_uid() {
  curl -s --max-time 10 "${BASE_URL}/api/v1/devices" 2>/dev/null \
    | python3 -c "
import json, sys
try:
    rows = (json.load(sys.stdin).get('data') or [])
except Exception:
    rows = []
for row in rows:
    if row.get('status') == 'online':
        print(row.get('uid') or '')
        break
" 2>/dev/null
}

# render_page <slug> <url>
render_page() {
  local slug="$1" url="$2"
  local out
  out="$(mktemp)"

  "${CHROME}" \
    --headless=new \
    --no-sandbox \
    --disable-gpu \
    --disable-dev-shm-usage \
    --virtual-time-budget=10000 \
    --dump-dom "${url}" > "${out}" 2>/dev/null

  local len
  len="$(wc -c < "${out}")"

  echo
  echo "=== 页面 ${slug} (${url}) — DOM ${len} bytes ==="

  check "${out}" "React 已挂载到 #root" 'id="root"><div[^>]*>'
  check "${out}" "#root 内有实际内容"   'id="root"><div[^>]*>.+'
  check "${out}" "左侧菜单有三项"       'sider-label">文件浏览'

  case "${slug}" in
    machines)
      check "${out}" "远程机器配置标题"   "远程机器连接配置"
      check "${out}" "新增机器按钮"       "新增机器"
      check "${out}" "机器表格表头"       "地址"
      check "${out}" "设备数列"           "设备数"
      check "${out}" "测试连接按钮"       "测试连接"
      ;;
    devices)
      check "${out}" "设备资源标题"       "设备资源管理"
      check "${out}" "唯一标识列"         "唯一标识 UID"
      check "${out}" "CPU 列"             ">CPU<"
      check "${out}" "内存列"             ">内存<"
      check "${out}" "浏览文件入口"       "浏览文件"
      check "${out}" "轮询提示"           "每 [0-9]+ 秒刷新一次运行状态"
      check "${out}" "设备名可点击编辑"   '<a class="ant-typography[^"]*"><span' 
      # The CPU / memory gauges are circular Progress widgets.
      check "${out}" "CPU 为圆形进度条"   "ant-progress-circle"
      check "${out}" "圆形进度条显示百分比" "ant-progress-text[^>]*title=\"[0-9.]+%\""
      # The memory column also carries the used / total amounts.
      check "${out}" "内存列含用量文字"   "共 [0-9.]+ [KMG]?B"
      ;;
    picker)
      check "${out}" "文件浏览标题"       "文件浏览"
      check "${out}" "提示先选设备"       "先选择要浏览文件的设备"
      check "${out}" "过滤输入框"         "按型号 / 序列号 / UID 过滤"
      check "${out}" "浏览文件按钮"       "浏览文件"
      ;;
    files)
      check "${out}" "文件浏览标题"       "文件浏览"
      check "${out}" "切换设备按钮"       "切换设备"
      check "${out}" "设备列表按钮"       "设备列表"
      check "${out}" "根目录面包屑"       "根目录"
      check "${out}" "上传按钮"           "上传到当前目录"
      check "${out}" "搜索框"             "按文件名搜索"
      check "${out}" "root 提权按钮"       "(尝试|取消) ?root"
      check "${out}" "新建目录按钮"       "新建目录"
      if [[ "${FILE_UID}" != "0000000000000000" ]]; then
        check "${out}" "重命名按钮"       "重命名"
        check "${out}" "删除按钮"         "anticon-delete"
      fi
      ;;
    settings)
      check "${out}" "平台设置标题"       "平台设置"
      check "${out}" "环境自检卡片"       "环境自检"
      check "${out}" "重新检查按钮"       "重新检查"
      check "${out}" "Python 检查项"      "Python 运行时"
      check "${out}" "adb 检查项"         "本机 adb"
      check "${out}" "数据目录检查项"     "数据目录写权限"
      ;;
  esac

  if [[ "${EXPECT_DEVICE}" == "yes" ]]; then
    case "${slug}" in
      devices|picker)
        check "${out}" "显示设备型号/序列号" "emulator-5554|Pixel_5|sdk_gphone"
        check "${out}" "显示设备唯一标识"   "[0-9a-f]{16}"
        ;;
    esac
  fi

  if [[ "${len}" -lt 2000 ]]; then
    echo "FAIL  DOM 过小，页面很可能未渲染 (${len} bytes)"
    FAILED=1
  fi

  echo "--- 片段 ---"
  grep -oE "<h1[^>]*>[^<]*</h1>|远程机器连接配置|设备资源管理|文件浏览|浏览文件|切换设备|新增机器|唯一标识 UID|emulator-5554|sdk_gphone[0-9_a-z]*|[0-9a-f]{16}" "${out}" | head -8

  rm -f "${out}"
}

if [[ -n "${ONLY_PAGE}" ]]; then
  case "${ONLY_PAGE}" in
    machines) render_page machines "${TARGET_URL:-${BASE_URL}/machines}" ;;
    devices) render_page devices "${TARGET_URL:-${BASE_URL}/devices}" ;;
    picker) render_page picker "${TARGET_URL:-${BASE_URL}/files}" ;;
    files)
      # The file rows (and therefore the row actions) only exist when the
      # page is opened for a real device, so use a real uid when there is one.
      FILE_UID="$(device_uid || echo 0000000000000000)"
      render_page files "${TARGET_URL:-${BASE_URL}/devices/${FILE_UID}/files}"
      ;;
    settings) render_page settings "${TARGET_URL:-${BASE_URL}/settings}" ;;
    *) echo "unknown page: ${ONLY_PAGE}" >&2; exit 2 ;;
  esac
elif [[ -n "${TARGET_URL}" ]]; then
  render_page custom "${TARGET_URL}"
else
  render_page machines "${BASE_URL}/machines"
  render_page devices "${BASE_URL}/devices"
  render_page picker "${BASE_URL}/files"
  # A real uid makes the file rows (and their actions) render.
  FILE_UID="$(device_uid || echo 0000000000000000)"
  render_page files "${BASE_URL}/devices/${FILE_UID}/files"
  render_page settings "${BASE_URL}/settings"
fi

echo
if [[ "${FAILED}" -eq 0 ]]; then
  echo "渲染检查通过：所有页面已在真实浏览器中渲染出内容"
else
  echo "渲染检查失败"
fi
exit "${FAILED}"
