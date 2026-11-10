/**
 * Render check with a device present.
 *
 * Boots a backend that serves one fake Android device, then opens the
 * frontend dev server in a real browser and asserts that the device card,
 * its CPU / memory bars and the file browser actually appear in the DOM.
 *
 * Usage: node scripts/render-check-with-device.mjs
 */
import { spawn } from "node:child_process";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import puppeteer from "puppeteer-core";

const BROWSER_PATH =
  process.env.BROWSER_PATH ??
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";

const BACKEND_PORT = 8010;
const FRONTEND_PORT = 10012;
const FRONTEND_URL = `http://127.0.0.1:${FRONTEND_PORT}/`;

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "afe-render-"));

const serverScript = path.join(tmp, "server.py");
fs.writeFileSync(
  serverScript,
  `
import os, sys
sys.path.insert(0, "${process.env.AFE_ROOT}")
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///${tmp}/db.sqlite"
os.environ["DATA_DIR"] = "${tmp}"
import uvicorn
from app.main import app
from app.state import get_transport_resolver
from app.transports.base import DeviceEntry, FileEntry, MetricsSample, TransferResult

class FakeTransport:
    kind = "local"
    def __init__(self):
        self._endpoint = "local:127.0.0.1:5037"
        self.tree = {
            "/": [FileEntry(name="sdcard", path="/sdcard", is_dir=True, size=4096)],
            "/sdcard": [FileEntry(name="hello.txt", path="/sdcard/hello.txt", is_dir=False, size=26)],
        }
    @property
    def endpoint(self): return self._endpoint
    async def list_devices(self):
        return [DeviceEntry(serial="emulator-5554", state="device", model="Pixel_5")]
    async def shell(self, serial, command, timeout=None): return ""
    async def list_dir(self, serial, path): return list(self.tree.get(path, []))
    async def pull(self, serial, device_path, local_path):
        open(local_path, "wb").write(b"hello")
        return TransferResult(ok=True, path=device_path, size=5, stored_at=local_path)
    async def push(self, serial, local_path, device_path):
        return TransferResult(ok=True, path=device_path, size=1, stored_at=device_path)
    async def connect(self, serial): return True, "connected"
    async def disconnect(self, serial): return True, "disconnected"
    async def metrics(self, serial):
        s = MetricsSample()
        s.raw_total_jiffies = 1000
        s.raw_idle_jiffies = 400
        s.memory_total_bytes = 8 * 1024 ** 3
        s.memory_available_bytes = 4 * 1024 ** 3
        s.memory_used_bytes = 4 * 1024 ** 3
        s.memory_percent = 50.0
        return s

get_transport_resolver()._local = FakeTransport()
uvicorn.run(app, host="0.0.0.0", port=${BACKEND_PORT}, log_level="warning")
`,
  "utf8",
);

function waitForPort(port, timeoutMs = 60000) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const attempt = () => {
      const socket = net.connect(port, "127.0.0.1");
      socket.once("connect", () => {
        socket.destroy();
        resolve(true);
      });
      socket.once("error", () => {
        socket.destroy();
        if (Date.now() > deadline) reject(new Error(`port ${port} timeout`));
        else setTimeout(attempt, 400);
      });
    };
    attempt();
  });
}

const python = process.env.AFE_PYTHON;
const backend = spawn(python, [serverScript], { stdio: "pipe" });
backend.stdout.on("data", () => {});
backend.stderr.on("data", () => {});

const browser = await puppeteer.launch({
  executablePath: BROWSER_PATH,
  headless: "new",
  args: ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
});

let exitCode = 0;
try {
  await waitForPort(BACKEND_PORT);
  await waitForPort(FRONTEND_PORT);

  const page = await browser.newPage();
  const pageErrors = [];
  const consoleErrors = [];
  page.on("pageerror", (e) => pageErrors.push(String(e?.stack ?? e)));
  page.on("console", (m) => {
    if (m.type() === "error") consoleErrors.push(m.text());
  });

  await page.goto(FRONTEND_URL, { waitUntil: "networkidle2", timeout: 45000 });
  // Trigger the device sync, then wait for the list to update.
  await page.evaluate(() => {
    const buttons = Array.from(document.querySelectorAll("button"));
    buttons.find((b) => b.textContent?.includes("同步"))?.click();
  });
  await new Promise((r) => setTimeout(r, 3000));

  const info = await page.evaluate(() => {
    const root = document.getElementById("root");
    const text = root?.innerText ?? "";
    return {
      rootHtmlLength: root?.innerHTML.length ?? -1,
      hasDeviceName: text.includes("Pixel_5"),
      hasSerial: text.includes("emulator-5554"),
      hasUid: /UID\s+[0-9a-f]{16}/.test(text),
      hasCpuBar: Boolean(document.querySelector(".metric-bar .ant-progress")),
      metricBarCount: document.querySelectorAll(".metric-bar .ant-progress").length,
      hasSyncButton: text.includes("同步"),
      text: text.slice(0, 500),
    };
  });

  console.log("=== 带设备场景渲染结果 ===");
  console.log(JSON.stringify(info, null, 2));
  console.log("\n=== 页面异常 ===");
  console.log(pageErrors.length ? pageErrors.join("\n") : "(无)");
  console.log("\n=== 控制台错误 ===");
  console.log(consoleErrors.length ? consoleErrors.join("\n") : "(无)");

  const checks = [
    ["页面已渲染", info.rootHtmlLength > 100],
    ["显示设备名", info.hasDeviceName],
    ["显示序列号", info.hasSerial],
    ["显示唯一标识 UID", info.hasUid],
    ["显示 CPU/内存进度条", info.metricBarCount >= 2],
    ["无页面异常", pageErrors.length === 0],
  ];
  console.log("\n=== 断言 ===");
  for (const [name, ok] of checks) {
    console.log(`${ok ? "PASS" : "FAIL"}  ${name}`);
    if (!ok) exitCode = 1;
  }
} catch (err) {
  console.error("渲染检查失败:", err);
  exitCode = 1;
} finally {
  await browser.close();
  backend.kill("SIGTERM");
  fs.rmSync(tmp, { recursive: true, force: true });
}

process.exit(exitCode);
