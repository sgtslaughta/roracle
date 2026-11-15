import "@testing-library/jest-dom/vitest";
import { afterAll, afterEach, beforeAll } from "vitest";
import { server } from "@/mocks/server";
import { resetMockState } from "@/mocks/state";
import { cleanup } from "@testing-library/react";

beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterEach(() => { cleanup(); server.resetHandlers(); resetMockState(); });
afterAll(() => server.close());

// jsdom gaps
if (!window.matchMedia) {
  window.matchMedia = ((q: string) => ({
    matches: false, media: q, onchange: null,
    addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}
class RO { observe() {} unobserve() {} disconnect() {} }
(globalThis as { ResizeObserver?: unknown }).ResizeObserver ??= RO;
class IO { observe() {} unobserve() {} disconnect() {} takeRecords() { return []; } }
(globalThis as { IntersectionObserver?: unknown }).IntersectionObserver ??= IO;
Element.prototype.scrollIntoView ??= function () {};
