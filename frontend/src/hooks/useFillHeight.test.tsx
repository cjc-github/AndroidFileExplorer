import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

import { measureReservedHeight, useFillHeight } from "./useFillHeight";

/** 用固定矩形覆盖 happy-dom 里恒为 0 的 getBoundingClientRect。 */
function stubHeight(el: Element, height: number) {
  el.getBoundingClientRect = () =>
    ({
      height,
      width: 0,
      top: 0,
      left: 0,
      right: 0,
      bottom: height,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    }) as DOMRect;
}

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
    true;
});

afterAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
    false;
});

describe("measureReservedHeight", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("adds the fixed header and the pagination (incl. its margins)", () => {
    const el = document.createElement("div");
    document.body.appendChild(el);
    el.innerHTML = `
      <div class="ant-table-header"></div>
      <div class="ant-table-body"></div>
      <div class="ant-table-pagination"></div>
    `;
    stubHeight(el.querySelector(".ant-table-header")!, 39);
    stubHeight(el.querySelector(".ant-table-pagination")!, 32);
    // 用 CSSOM 设置，确保 happy-dom 展开 shorthand 后能读出上下 margin
    const pagination = el.querySelector<HTMLElement>(".ant-table-pagination")!;
    pagination.style.margin = "8px 0";

    // 39 表头 + 32 分页 + 上下 margin 8 + 8
    expect(measureReservedHeight(el)).toBe(87);
  });

  it("counts the header alone for tables without pagination", () => {
    const el = document.createElement("div");
    el.innerHTML = `<div class="ant-table-header"></div><div class="ant-table-body"></div>`;
    stubHeight(el.querySelector(".ant-table-header")!, 39);

    expect(measureReservedHeight(el)).toBe(39);
  });

  it("reserves nothing when neither header nor pagination rendered", () => {
    const el = document.createElement("div");
    el.innerHTML = `<div class="ant-table-body"></div>`;

    expect(measureReservedHeight(el)).toBe(0);
  });
});

describe("useFillHeight", () => {
  // TS 的 DOM lib 没有导出 MutationObserverCallback，这里内联同样的签名
  type MutationCallback = (
    records: MutationRecord[],
    observer: MutationObserver,
  ) => void;

  let root: Root | null = null;
  let container: HTMLElement | null = null;
  const resizeCallbacks: ResizeObserverCallback[] = [];
  const mutationCallbacks: MutationCallback[] = [];

  beforeEach(() => {
    resizeCallbacks.length = 0;
    mutationCallbacks.length = 0;
    vi.stubGlobal(
      "ResizeObserver",
      class {
        constructor(callback: ResizeObserverCallback) {
          resizeCallbacks.push(callback);
        }
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    );
    vi.stubGlobal(
      "MutationObserver",
      class {
        constructor(callback: MutationCallback) {
          mutationCallbacks.push(callback);
        }
        observe() {}
        disconnect() {}
        takeRecords() {
          return [];
        }
      },
    );
  });

  afterEach(() => {
    act(() => root?.unmount());
    root = null;
    container = null;
    document.body.innerHTML = "";
    vi.unstubAllGlobals();
  });

  function Probe({ minHeight = 10 }: { minHeight?: number }) {
    const { ref, height } = useFillHeight<HTMLDivElement>(minHeight);
    return (
      <div ref={ref} data-height={height}>
        <div className="ant-table-header" />
        <div className="ant-table-pagination" style={{ margin: "8px 0" }} />
      </div>
    );
  }

  async function renderProbe(minHeight?: number) {
    container = document.createElement("div");
    document.body.appendChild(container);
    await act(async () => {
      root = createRoot(container!);
      root.render(<Probe minHeight={minHeight} />);
    });

    const fill = document.querySelector<HTMLElement>("[data-height]")!;
    stubHeight(fill, 600);
    stubHeight(fill.querySelector(".ant-table-header")!, 39);
    stubHeight(fill.querySelector(".ant-table-pagination")!, 32);
    return fill;
  }

  async function flushResize() {
    await act(async () => {
      for (const callback of resizeCallbacks) callback([], {} as ResizeObserver);
    });
  }

  async function flushMutation(added: Element) {
    const record = { addedNodes: [added] } as unknown as MutationRecord;
    await act(async () => {
      for (const callback of mutationCallbacks) callback([record], null as never);
    });
  }

  it("feeds scroll.y with the container height minus header and pagination", async () => {
    await renderProbe();
    await flushResize();

    expect(document.querySelector<HTMLElement>("[data-height]")!.dataset.height).toBe("513");
  });

  it("keeps the configured minimum height", async () => {
    await renderProbe(1000);
    await flushResize();

    expect(document.querySelector<HTMLElement>("[data-height]")!.dataset.height).toBe("1000");
  });

  it("leaves the minimum untouched when the reserved chrome eats the box", async () => {
    const fill = await renderProbe();
    stubHeight(fill, 50); // 50 - 87 < 0，不回填
    await flushResize();

    expect(fill.dataset.height).toBe("10");
  });

  it("subtracts a pagination bar that shows up only after data arrives", async () => {
    function LateProbe() {
      const { ref, height } = useFillHeight<HTMLDivElement>(10);
      return (
        <div ref={ref} data-late-height={height}>
          <div className="ant-table-header" />
        </div>
      );
    }

    container = document.createElement("div");
    document.body.appendChild(container);
    await act(async () => {
      root = createRoot(container!);
      root.render(<LateProbe />);
    });

    const fill = document.querySelector<HTMLElement>("[data-late-height]")!;
    stubHeight(fill, 600);
    stubHeight(fill.querySelector(".ant-table-header")!, 39);
    await flushResize();
    // 还没有分页条：600 - 39 = 561
    expect(fill.dataset.lateHeight).toBe("561");

    // 数据到达后 antd 才插入分页条（含上下 margin）
    const pagination = document.createElement("div");
    pagination.className = "ant-table-pagination";
    pagination.style.margin = "8px 0";
    stubHeight(pagination, 32);
    fill.appendChild(pagination);
    await flushMutation(pagination);

    // 600 - 39 表头 - 32 分页 - 16 margin = 513
    expect(fill.dataset.lateHeight).toBe("513");
  });

  it("starts measuring when the table wrapper mounts later than the page", async () => {
    // 文件浏览页先渲染 Empty（设备离线/加载失败），条件满足后才出现
    // .table-fill —— ref 挂晚了也必须接上观察者，否则高度永远停在下限。
    function ToggleProbe({ showTable }: { showTable: boolean }) {
      const { ref, height } = useFillHeight<HTMLDivElement>(10);
      if (!showTable) return <div data-empty="1" />;
      return (
        <div ref={ref} data-toggle-height={height}>
          <div className="ant-table-header" />
        </div>
      );
    }

    container = document.createElement("div");
    document.body.appendChild(container);
    await act(async () => {
      root = createRoot(container!);
      root.render(<ToggleProbe showTable={false} />);
    });
    expect(document.querySelector("[data-empty]")).not.toBeNull();

    await act(async () => {
      root!.render(<ToggleProbe showTable={true} />);
    });

    const fill = document.querySelector<HTMLElement>("[data-toggle-height]")!;
    stubHeight(fill, 600);
    stubHeight(fill.querySelector(".ant-table-header")!, 39);
    await flushResize();

    expect(fill.dataset.toggleHeight).toBe("561");
  });
});
