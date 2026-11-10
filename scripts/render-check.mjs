/**
 * Headless render check for the AndroidFileExplorer frontend.
 *
 * Opens the deployed dev server in a real browser, collects console errors
 * and failed requests, and reports what actually got rendered into #root.
 *
 * Usage: node scripts/render-check.mjs <url>
 */
import puppeteer from "puppeteer-core";

const url = process.argv[2] ?? "http://192.168.114.128:10012/";
const browserPath =
  process.env.BROWSER_PATH ??
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";

const browser = await puppeteer.launch({
  executablePath: browserPath,
  headless: "new",
  args: ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
});

const consoleErrors = [];
const pageErrors = [];
const failedRequests = [];

try {
  const page = await browser.newPage();
  page.on("console", (msg) => {
    if (msg.type() === "error" || msg.type() === "warning") {
      consoleErrors.push(`[${msg.type()}] ${msg.text()}`);
    }
  });
  page.on("pageerror", (err) => pageErrors.push(String(err?.stack ?? err)));
  page.on("requestfailed", (req) =>
    failedRequests.push(`${req.method()} ${req.url()} -> ${req.failure()?.errorText}`),
  );
  page.on("response", (res) => {
    if (res.status() >= 400) failedRequests.push(`${res.status()} ${res.url()}`);
  });

  await page.goto(url, { waitUntil: "networkidle2", timeout: 45000 });
  await new Promise((r) => setTimeout(r, 2500));

  const info = await page.evaluate(() => {
    const root = document.getElementById("root");
    return {
      title: document.title,
      rootChildren: root ? root.children.length : -1,
      rootHtmlLength: root ? root.innerHTML.length : -1,
      rootText: root ? root.innerText.slice(0, 400) : "(no root)",
      bodyTextLength: document.body.innerText.trim().length,
      hasAppShell: Boolean(document.querySelector(".app-shell")),
      hasDeviceList: Boolean(document.querySelector(".app-side")),
      headerText: document.querySelector("header h1")?.textContent ?? null,
    };
  });

  console.log("=== URL ===");
  console.log(url);
  console.log("\n=== 渲染结果 ===");
  console.log(JSON.stringify(info, null, 2));
  console.log("\n=== 控制台错误/警告 ===");
  console.log(consoleErrors.length ? consoleErrors.join("\n") : "(无)");
  console.log("\n=== 页面异常 ===");
  console.log(pageErrors.length ? pageErrors.join("\n") : "(无)");
  console.log("\n=== 失败请求 ===");
  console.log(failedRequests.length ? failedRequests.join("\n") : "(无)");

  const ok = info.rootChildren > 0 && info.rootHtmlLength > 100;
  console.log(`\n=== 结论: ${ok ? "页面已渲染" : "页面空白（未渲染）"} ===`);
  process.exitCode = ok ? 0 : 1;
} finally {
  await browser.close();
}
