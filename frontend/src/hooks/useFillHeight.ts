import { useCallback, useEffect, useState } from "react";

/**
 * 量出 antd Table 中「不受 scroll.y 约束」的那部分高度。
 *
 * antd 的 `scroll.y` 只作用于 `.ant-table-body` 的 max-height；固定表头
 * （`.ant-table-header`）和分页条（`.ant-table-pagination`）是它的兄弟节点，
 * 高度并不计入 scroll.y。若直接把容器高度当 scroll.y 回填，
 * 「表头 + 表体 + 分页」会超出容器，被 `.table-fill` 的 overflow:hidden
 * 裁掉底部（分页被切掉一半）。所以回填前要先把这部分预留高度扣掉。
 */
export function measureReservedHeight(el: HTMLElement): number {
  let reserved = 0;

  const header = el.querySelector<HTMLElement>(".ant-table-header");
  if (header) reserved += Math.ceil(header.getBoundingClientRect().height);

  const pagination = el.querySelector<HTMLElement>(".ant-table-pagination");
  if (pagination) {
    const style = getComputedStyle(pagination);
    reserved += Math.ceil(pagination.getBoundingClientRect().height);
    // 分页条自带上下 margin，同样占容器高度
    reserved += Math.ceil(parseFloat(style.marginTop) || 0);
    reserved += Math.ceil(parseFloat(style.marginBottom) || 0);
  }

  return reserved;
}

/**
 * 上报 `ref` 所指元素可用于表格体（scroll.y）的高度。
 *
 * 每个页面都是填满 `.app-main` 的 flex 纵向列；`ref` 挂在包住表格的
 * flex 增长容器上，它的高度跟随视口。把「容器高度 − 表头/分页预留高度」
 * 喂给 antd 的 `scroll.y`，表体就会在容器内部滚动，而不是把整页撑高或
 * 被容器裁掉底边。
 */
export function useFillHeight<T extends HTMLElement>(minHeight = 240) {
  // 用 callback ref 而不是 useRef：表格容器可能晚于首帧挂载（文件浏览页先
  // 渲染 Empty，条件满足后才出现 .table-fill），ref 的内容要参与 effect
  // 依赖，否则 observer 永远接不上、高度停在下限。
  const [el, setEl] = useState<T | null>(null);
  const [height, setHeight] = useState<number>(minHeight);
  const ref = useCallback((node: T | null) => setEl(node), []);

  useEffect(() => {
    if (!el) return;

    const chromeSelectors = [".ant-table-header", ".ant-table-pagination"];

    const report = () => {
      const total = Math.round(el.getBoundingClientRect().height);
      const next = total - measureReservedHeight(el);
      if (next > 0) {
        // scroll.y 对外暴露的是经过 minHeight 兜底的值，--fill-height 必须
        // 取同一个兜底值，否则表体的 min-height 会低于 max-height，撑满失效。
        const clamped = Math.max(minHeight, next);
        setHeight(clamped);
        // 交给样式表让表体撑满这块空间，设备/文件很少时也不会在表格下方
        // 露出页面背景。
        el.style.setProperty("--fill-height", `${clamped}px`);
      }
    };

    const observer = new ResizeObserver(report);
    // 容器（视口/侧栏变化）与表头、分页自身尺寸变化（列内容换行、
    // 分页显示与否）都会改变可用高度，三者都要观察。
    const watched = new Set<Element>();
    const watchChrome = () => {
      for (const selector of chromeSelectors) {
        const node = el.querySelector<HTMLElement>(selector);
        if (node && !watched.has(node)) {
          watched.add(node);
          observer.observe(node);
        }
      }
    };
    observer.observe(el);
    watchChrome();

    // 表头/分页可能晚于本 effect 挂载，也可能随数据到达才出现
    // （antd 在单页时按 hideOnSinglePage 藏起分页），所以要常驻监听。
    // 但虚拟表格滚动会频繁增删行，只在「新增节点里确实有表头或分页」
    // 时才重算，避免滚动时反复读布局。
    const mutation = new MutationObserver((records) => {
      for (const record of records) {
        for (const node of record.addedNodes) {
          if (node.nodeType !== Node.ELEMENT_NODE) continue;
          const added = node as Element;
          const hit =
            chromeSelectors.some((selector) => added.matches?.(selector)) ||
            Boolean(added.querySelector?.(chromeSelectors.join(",")));
          if (hit) {
            watchChrome();
            report();
            return;
          }
        }
      }
    });
    mutation.observe(el, { childList: true, subtree: true });

    return () => {
      observer.disconnect();
      mutation.disconnect();
    };
  }, [el]);

  return { ref, height: Math.max(minHeight, height) };
}
