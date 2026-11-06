import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll } from "vitest";
import { setUnauthorizedHandler } from "@/api/client";
import { server } from "./server";

// Node's fetch only accepts absolute URLs, a browser resolves relative ones. Do what a browser does.
const nodeFetch = globalThis.fetch;
globalThis.fetch = (input: RequestInfo | URL, init?: RequestInit) =>
  nodeFetch(typeof input === "string" && input.startsWith("/") ? new URL(input, window.location.origin) : input, init);

beforeAll(() => server.listen({ onUnhandledFrame: "error" }));

afterEach(() => {
  cleanup();
  server.resetHandlers();
  setUnauthorizedHandler(null);
  localStorage.clear();
  document.documentElement.classList.remove("dark");
  for (const cookie of document.cookie.split("; ")) {
    const name = cookie.split("=")[0];
    if (name) document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/`;
  }
});

afterAll(() => server.close());

// jsdom does no layout, so Recharts' ResponsiveContainer would measure 0x0 and draw nothing.
// This stand-in reports a fixed size, which lets tests see the real bars.
class FixedSizeResizeObserver {
  constructor(private callback: ResizeObserverCallback) {}
  observe(target: Element) {
    const rect = { width: 600, height: 240, top: 0, left: 0, bottom: 240, right: 600, x: 0, y: 0, toJSON() {} };
    this.callback(
      [{ target, contentRect: rect, borderBoxSize: [], contentBoxSize: [], devicePixelContentBoxSize: [] }],
      this as unknown as ResizeObserver,
    );
  }
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver = FixedSizeResizeObserver as unknown as typeof ResizeObserver;

// jsdom has no matchMedia; the theme code asks the operating-system preference through it.
if (!window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }) as MediaQueryList;
}
