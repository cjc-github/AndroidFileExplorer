"""One-shot patcher: make the rename interaction check wait for the modal.

The rename modal opens with the current name already in the input.  The
check set the new name right away, which raced the render: React restored
the old value and the request went out with source == target.

The check now waits until the input really shows the old name before it
types the new one.
"""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
TARGET = ROOT / "scripts" / "file-browser-check.mjs"
source = TARGET.read_text(encoding="utf-8")

OLD = """  const RENAMED = `${DIR_NAME}-renamed`;
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
  `);"""

NEW = """  const RENAMED = `${DIR_NAME}-renamed`;
  // The modal renders with the current name; wait for it, otherwise the
  // check races the render and React restores the old value.
  let modalReady = false;
  for (let attempt = 0; attempt < 20 && !modalReady; attempt += 1) {
    modalReady = await cdp.eval(`
      (() => {
        const input = document.querySelector('.ant-modal-content input');
        return Boolean(input) && input.value === ${JSON.stringify(DIR_NAME)};
      })()
    `);
    if (!modalReady) await sleep(300);
  }
  check("重命名弹窗已填入原名称", Boolean(modalReady));

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
  `);"""

if OLD in source:
    source = source.replace(OLD, NEW, 1)
    TARGET.write_text(source, encoding="utf-8")
    print("PATCHED")
else:
    print("ANCHOR MISSING")
    raise SystemExit(1)
