import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot } from "react-dom/client";

import { ErrorBoundary } from "./ErrorBoundary";

function Boom(): never {
  throw new Error("boom");
}

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
    true;
});

afterAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
    false;
});

describe("ErrorBoundary", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    document.body.innerHTML = "";
  });

  it("renders children while nothing crashes", async () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    await act(async () => {
      createRoot(container).render(
        <ErrorBoundary>
          <p>hello</p>
        </ErrorBoundary>,
      );
    });
    expect(container.textContent).toBe("hello");
    expect(spy).not.toHaveBeenCalled();
  });

  it("shows a recoverable fallback when a child crashes", async () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    vi.spyOn(console, "error").mockImplementation(() => {});
    await act(async () => {
      createRoot(container).render(
        <ErrorBoundary>
          <Boom />
        </ErrorBoundary>,
      );
    });
    expect(container.textContent).toContain("页面出错了");
    expect(container.textContent).toContain("boom");
    expect(container.textContent).toContain("刷新页面");
  });
});
