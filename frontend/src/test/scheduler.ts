/** react-query scheduler that drops callbacks fired after jsdom teardown (no `window`). Same macrotask timing as the default. */
export function guardedScheduler(cb: () => void): void {
  setTimeout(() => { if (typeof window !== "undefined") cb(); }, 0);
}
