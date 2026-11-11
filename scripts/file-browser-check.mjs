/**
 * Interactive check of the file browser - driven through the Chrome DevTools
 * Protocol over a raw WebSocket.
 *
 * Why not puppeteer: the npm mirror in this environment does not carry every
 * @puppeteer/browsers release, and the check must stay dependency free so it
 * can run anywhere the project runs.
 *
 * Usage: node scripts/file-browser-check.mjs <baseUrl> <uid>
 *
 * NOTE: antd inserts a space between two Chinese characters ("跳 转"), so
 * every text comparison strips all whitespace before matching.
 */
import { spawn } from "node:child_process";
import { setTimeout as sleep } from "node:timers/promises";

const BASE = process.argv[2] ?? "http://127.0.0.1:10012";
const UID = process.argv[3] ?? "";
const CHROME = process.env.BROWSER_PATH ?? "/usr/bin/google-chrome";
const PORT = Number(process.env.CDP_PORT ?? 9333);

const checks = [];
const check = (name, ok, extra = "") => {
  checks.push({ name, ok, extra });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${extra ? `  ${extra}` : ""}`);
};

// ---------------------------------------------------------------- CDP client

class Cdp {
  constructor(ws) {
    this.ws = ws;
    this.id = 0;
    this.pending = new Map();
    this.events = [];
    ws.addEventListener("message", (event) => {
      const msg = JSON.parse(event.data);
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        if (msg.error) reject(new Error(JSON.stringify(msg.error)));
        else resolve(msg.result);
      } else if (msg.method) {
        this.events.push(msg);
      }
    });
  }

  send(method, params = {}) {
    const id = ++this.id;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
      setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id);
          reject(new Error(`timeout: ${method}`));
        }
      }, 30000);
    });
  }

  async eval(expression) {
    const result = await this.send("Runtime.evaluate", {
      expression,
      awaitPromise: true,
      returnByValue: true,
    });
    if (result.exceptionDetails) {
      throw new Error(
        `eval failed: ${JSON.stringify(result.exceptionDetails).slice(0, 300)}`,
      );
    }
    return result.result?.value;
  }
}

async function waitForCdp(port, timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/json/version`);
      if (response.ok) return await response.json();
    } catch {
      // not up yet
    }
    await sleep(300);
  }
  throw new Error("chrome devtools endpoint did not come up");
}

// ------------------------------------------------------------------- main

const chrome = spawn(
  CHROME,
  [
    "--headless=new",
    "--no-sandbox",
    "--disable-gpu",
    "--disable-dev-shm-usage",
    `--remote-debugging-port=${PORT}`,
    "--remote-allow-origins=*",
    "about:blank",
  ],
  { stdio: "ignore" },
);

let cdp;
try {
  const version = await waitForCdp(PORT);
  const target = await (
    await fetch(`http://127.0.0.1:${PORT}/json/new?${encodeURIComponent(BASE)}`, {
      method: "PUT",
    })
  ).json();

  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    ws.addEventListener("open", resolve, { once: true });
    ws.addEventListener("error", reject, { once: true });
  });
  cdp = new Cdp(ws);

  await cdp.send("Page.enable");
  await cdp.send("Runtime.enable");
  await cdp.send("Network.enable");

  // Navigate.
  await cdp.send("Page.navigate", { url: `${BASE}/devices/${UID}/files` });
  await sleep(3500);

  const fileRequests = [];
  const collectRequests = () => {
    for (const event of cdp.events) {
      if (event.method === "Network.requestWillBeSent") {
        const url = event.params?.request?.url ?? "";
        if (url.includes("/files")) fileRequests.push(url);
      }
    }
    cdp.events = [];
  };
  collectRequests();

  const hasPathBox = await cdp.eval(
    `Boolean(document.querySelector('input[placeholder*="直接输入路径"]'))`,
  );
  check("存在路径输入框", Boolean(hasPathBox));

  // Fill the path box and press the jump button.
  await cdp.eval(`
    (() => {
      const input = document.querySelector('input[placeholder*="直接输入路径"]');
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype, 'value').set;
      setter.call(input, '/data/local/tmp');
      input.dispatchEvent(new Event('input', { bubbles: true }));
      const button = Array.from(document.querySelectorAll('button')).find(
        (b) => b.textContent?.replace(/\\s+/g, '') === '跳转');
      if (button) button.click();
      return Boolean(button);
    })()
  `);
  await sleep(3500);
  collectRequests();

  const requestedTmp = fileRequests.some((u) =>
    decodeURIComponent(u).includes("path=/data/local/tmp"),
  );
  check(
    "已按 /data/local/tmp 发起请求",
    requestedTmp,
    `${fileRequests.length} 个文件请求`,
  );

  const afterJump = await cdp.eval(`(() => {
    const text = document.body.innerText;
    return {
      hasTmp: text.includes('afl-frida-trace.so') || text.includes('aflplusplus'),
      hasData: text.includes('data'),
      hasLocal: text.includes('local'),
      hasError: text.includes('权限不足'),
    };
  })()`);
  check("跳转到 /data/local/tmp 后列出内容", Boolean(afterJump?.hasTmp));
  check("面包屑包含 data / local", Boolean(afterJump?.hasData && afterJump?.hasLocal));
  check("可访问路径不误报权限错误", !afterJump?.hasError);

  // Click the "data" breadcrumb -> the permission error must show up.
  const breadcrumbClicked = await cdp.eval(`
    (() => {
      const links = Array.from(document.querySelectorAll('.ant-breadcrumb-link'));
      const target = links.find((l) => l.textContent?.replace(/\\s+/g, '') === 'data');
      if (target) target.click();
      return Boolean(target);
    })()
  `);
  check("找到「data」面包屑", Boolean(breadcrumbClicked));
  await sleep(3500);
  collectRequests();

  const requestedData = fileRequests.some((u) =>
    decodeURIComponent(u).includes("path=/data&") ||
    decodeURIComponent(u).endsWith("path=/data"),
  );
  check("点击面包屑后按 /data 发起请求", requestedData);

  const afterData = await cdp.eval(`(() => {
    const text = document.body.innerText;
    const alert = document.querySelector('.ant-alert');
    return {
      hasPermissionAlert: text.includes('权限不足'),
      hasRootHint: text.includes('root'),
      alertText: alert?.innerText?.slice(0, 240) ?? '',
    };
  })()`);
  check("点击 data 面包屑后提示权限不足", Boolean(afterData?.hasPermissionAlert));
  check("提示中包含 root 指引", Boolean(afterData?.hasRootHint));
  if (afterData?.alertText) {
    console.log(`      提示内容: ${afterData.alertText.replace(/\n/g, " | ")}`);
  }

  // ---- the file operations -----------------------------------------------
  // Go back to a writable directory first.
  await cdp.eval(`
    (() => {
      const input = document.querySelector('input[placeholder*="直接输入路径"]');
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype, 'value').set;
      setter.call(input, '/sdcard/Download');
      input.dispatchEvent(new Event('input', { bubbles: true }));
      const button = Array.from(document.querySelectorAll('button')).find(
        (b) => b.textContent?.replace(/\\s+/g, '') === '跳转');
      if (button) button.click();
      return Boolean(button);
    })()
  `);
  await sleep(3000);

  const DIR_NAME = `afe-ui-check-${Date.now()}`;

  // Open the "new directory" modal.
  const opened = await cdp.eval(`
    (() => {
      const button = Array.from(document.querySelectorAll('button')).find(
        (b) => b.textContent?.replace(/\\s+/g, '') === '新建目录');
      if (button) button.click();
      return Boolean(button);
    })()
  `);
  check("找到「新建目录」按钮", Boolean(opened));
  await sleep(1200);

  // Type the name and confirm.
  const created = await cdp.eval(`
    (() => {
      const input = document.querySelector('.afe-modal-mkdir .ant-modal-content input, .afe-modal-mkdir input');
      if (!input) return false;
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype, 'value').set;
      setter.call(input, ${JSON.stringify(DIR_NAME)});
      input.dispatchEvent(new Event('input', { bubbles: true }));
      const ok = Array.from(document.querySelectorAll('.afe-modal-mkdir button')).find(
        (b) => b.textContent?.replace(/\\s+/g, '') === '创建');
      if (ok) ok.click();
      return Boolean(ok);
    })()
  `);
  check("在弹窗中输入目录名并点击创建", Boolean(created));
  await sleep(4000);

  const listed = await cdp.eval(
    `document.body.innerText.includes(${JSON.stringify(DIR_NAME)})`,
  );
  check("新建的目录出现在列表中", Boolean(listed));

  // Rename it through the row action.
  //
  // NOTE: the table uses antd's `virtual` rendering, so a data row is a
  // <div class="ant-table-row">, not a <tr>. Both shapes carry the
  // `ant-table-row` class, so select on that instead of the tag name.
  const renamed = await cdp.eval(`
    (() => {
      const row = Array.from(document.querySelectorAll('.ant-table-row')).find((tr) =>
        tr.innerText?.includes(${JSON.stringify(DIR_NAME)}));
      if (!row) return false;
      const button = Array.from(row.querySelectorAll('button')).find(
        (b) => b.textContent?.replace(/\\s+/g, '') === '重命名');
      if (button) button.click();
      return Boolean(button);
    })()
  `);
  check("找到该行的「重命名」按钮", Boolean(renamed));
  await sleep(1200);

  // The new name must NOT contain the old name as a substring, otherwise
  // "the old name is gone" can never be true and the row lookup matches
  // the wrong entry.
  const RENAMED = `afe-renamed-${Date.now()}`;
  // The modal renders with the current name; wait for it, otherwise the
  // check races the render and React restores the old value.
  let modalReady = false;
  for (let attempt = 0; attempt < 20 && !modalReady; attempt += 1) {
    modalReady = await cdp.eval(`
      (() => {
        const input = document.querySelector('.afe-modal-rename .ant-modal-content input, .ant-modal-rename input');
        return Boolean(input) && input.value === ${JSON.stringify(DIR_NAME)};
      })()
    `);
    if (!modalReady) await sleep(300);
  }
  check("重命名弹窗已填入原名称", Boolean(modalReady));

  const submitted = await cdp.eval(`
    (() => {
      const input = document.querySelector('.afe-modal-rename .ant-modal-content input, .ant-modal-rename input');
      if (!input) return false;
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype, 'value').set;
      setter.call(input, ${JSON.stringify(RENAMED)});
      input.dispatchEvent(new Event('input', { bubbles: true }));
      const ok = Array.from(document.querySelectorAll('.afe-modal-rename button')).find(
        (b) => b.textContent?.replace(/\\s+/g, '') === '保存');
      if (ok) ok.click();
      return Boolean(ok);
    })()
  `);
  check("提交重命名", Boolean(submitted));
  await sleep(4000);

  const renamedListed = await cdp.eval(`
    (() => {
      const text = document.body.innerText;
      return text.includes(${JSON.stringify(RENAMED)}) &&
             !text.includes(${JSON.stringify(DIR_NAME)});
    })()
  `);
  check("目录已用新名字出现", Boolean(renamedListed));

  // Delete it again.
  const deleted = await cdp.eval(`
    (() => {
      const row = Array.from(document.querySelectorAll('.ant-table-row')).find((tr) =>
        tr.innerText?.includes(${JSON.stringify(RENAMED)}));
      if (!row) return false;
      const button = Array.from(row.querySelectorAll('button')).find(
        (b) => b.querySelector('.anticon-delete'));
      if (button) button.click();
      return Boolean(button);
    })()
  `);
  check("找到该行的「删除」按钮", Boolean(deleted));
  await sleep(1200);

  const confirmed = await cdp.eval(`
    (() => {
      const button = Array.from(document.querySelectorAll('.ant-popover button')).find(
        (b) => b.textContent?.replace(/\\s+/g, '') === '删除');
      if (button) button.click();
      return Boolean(button);
    })()
  `);
  check("在确认框中点击删除", Boolean(confirmed));
  await sleep(4000);

  const gone = await cdp.eval(
    `!document.body.innerText.includes(${JSON.stringify(RENAMED)})`,
  );
  check("目录已从列表中消失", Boolean(gone));
} catch (err) {
  console.error("检查失败:", err);
  checks.push({ name: "执行", ok: false });
} finally {
  try {
    chrome.kill("SIGTERM");
  } catch {
    // ignore
  }
}

const failed = checks.filter((c) => !c.ok).length;
console.log(
  `\n=== ${failed === 0 ? "文件浏览交互检查通过" : `文件浏览交互检查失败 (${failed} 项)`} ===`,
);
process.exit(failed === 0 ? 0 : 1);
