import { expect, it, vi } from "vitest";
import { guardedScheduler } from "./scheduler";

it("skips callbacks after teardown", () => {
  vi.useFakeTimers(); const cb = vi.fn();
  guardedScheduler(cb); vi.stubGlobal("window", undefined); vi.runAllTimers(); vi.unstubAllGlobals();
  expect(cb).not.toHaveBeenCalled();
  guardedScheduler(cb); vi.runAllTimers(); expect(cb).toHaveBeenCalledOnce(); vi.useRealTimers();
});
