import { useSyncExternalStore } from "react";

/** Minimal external store (like the reader store): `use(selector)` re-renders only when the selected value changes. */
export function createStore<T>(init: T) {
  let state = init;
  const subs = new Set<() => void>();
  const subscribe = (f: () => void) => { subs.add(f); return () => { subs.delete(f); }; };
  return {
    get: () => state,
    set(next: T | ((s: T) => T)) {
      state = typeof next === "function" ? (next as (s: T) => T)(state) : next;
      subs.forEach((f) => f());
    },
    reset() { this.set(init); },
    use: <R,>(select: (s: T) => R): R => useSyncExternalStore(subscribe, () => select(state)),
  };
}
