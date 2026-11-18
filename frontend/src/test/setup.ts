import "@testing-library/jest-dom/vitest";
import { afterAll, afterEach, beforeAll } from "vitest";
import { server } from "@/mocks/server";
import { resetMockState } from "@/mocks/state";
import { helpStore } from "@/features/help/helpStore";
import { cleanup, configure } from "@testing-library/react";
import { notifyManager } from "@tanstack/react-query";
import { guardedScheduler } from "./scheduler";

notifyManager.setScheduler(guardedScheduler);

// findBy*/waitFor default to 1 s, which cold lazy imports (pdf view, markdown) exceed when workers are busy: the source of a flaky reader test
configure({ asyncUtilTimeout: 5000 });

beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterEach(() => { cleanup(); helpStore.reset(); server.resetHandlers(); resetMockState(); });
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
URL.createObjectURL ??= () => "blob:jsdom";
URL.revokeObjectURL ??= () => {};
window.scrollTo = (() => {}) as typeof window.scrollTo;

// no canvas in jsdom
HTMLCanvasElement.prototype.getContext = (() => null) as unknown as typeof HTMLCanvasElement.prototype.getContext;

// jsdom's FormData/File are not understood by Node's fetch/undici (multipart parse asserts on the File class):
// use Node's own implementations for uploads in tests.
import { File as NodeFile } from "node:buffer";
const NodeFormData = Object.getPrototypeOf(
  await new Response("a=1", { headers: { "content-type": "application/x-www-form-urlencoded" } }).formData(),
).constructor;
globalThis.FormData = NodeFormData;
globalThis.File = NodeFile as unknown as typeof File;
