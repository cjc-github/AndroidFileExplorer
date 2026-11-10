"""One-shot patcher: add the file operation interaction checks.

The render check proves the buttons exist; this proves they WORK: a directory
is created through the UI, it shows up in the table, and it can be deleted
again.
"""

import pathlib

# scripts/patches/ -> scripts/ -> the project root
ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
TARGET = ROOT / "scripts" / "file-browser-check.mjs"
source = TARGET.read_text(encoding="utf-8")

ANCHOR = """  if (afterData?.alertText) {
    console.log(`      提示内容: ${afterData.alertText.replace(/\\n/g, " | ")}`);
  }
} catch (err) {"""

NEW = """  if (afterData?.alertText) {
    console.log(`      提示内容: ${afterData.alertText.replace(/\\n/g, " | ")}`);
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
        (b) => b.textContent?.replace(/\\\\s+/g, '') === '跳转');
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
        (b) => b.textContent?.replace(/\\\\s+/g, '') === '新建目录');
      if (button) button.click();
      return Boolean(button);
    })()
  `);
  check("找到「新建目录」按钮", Boolean(opened));
  await sleep(1200);

  // Type the name and confirm.
  const created = await cdp.eval(`
    (() => {
      const input = document.querySelector('.ant-modal-content input');
      if (!input) return false;
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype, 'value').set;
      setter.call(input, ${JSON.stringify(DIR_NAME)});
      input.dispatchEvent(new Event('input', { bubbles: true }));
      const ok = Array.from(document.querySelectorAll('.ant-modal-content button')).find(
        (b) => b.textContent?.replace(/\\\\s+/g, '') === '创建');
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
  const renamed = await cdp.eval(`
    (() => {
      const row = Array.from(document.querySelectorAll('tr')).find((tr) =>
        tr.innerText?.includes(${JSON.stringify(DIR_NAME)}));
      if (!row) return false;
      const button = Array.from(row.querySelectorAll('button')).find(
        (b) => b.textContent?.replace(/\\\\s+/g, '') === '重命名');
      if (button) button.click();
      return Boolean(button);
    })()
  `);
  check("找到该行的「重命名」按钮", Boolean(renamed));
  await sleep(1200);

  const RENAMED = `${DIR_NAME}-renamed`;
  const submitted = await cdp.eval(`
    (() => {
      const input = document.querySelector('.ant-modal-content input');
      if (!input) return false;
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype, 'value').set;
      setter.call(input, ${JSON.stringify(RENAMED)});
      input.dispatchEvent(new Event('input', { bubbles: true }));
      const ok = Array.from(document.querySelectorAll('.ant-modal-content button')).find(
        (b) => b.textContent?.replace(/\\\\s+/g, '') === '保存');
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
      const row = Array.from(document.querySelectorAll('tr')).find((tr) =>
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
        (b) => b.textContent?.replace(/\\\\s+/g, '') === '删除');
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
} catch (err) {"""

if ANCHOR in source:
    source = source.replace(ANCHOR, NEW, 1)
    TARGET.write_text(source, encoding="utf-8")
    print("PATCHED")
else:
    print("ANCHOR MISSING")
    raise SystemExit(1)
