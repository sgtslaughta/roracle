import { useSyncExternalStore } from "react";

/** True while the media query matches; re-renders on change. */
export function useMedia(query: string): boolean {
  return useSyncExternalStore(
    (cb) => { const m = window.matchMedia?.(query); m?.addEventListener?.("change", cb); return () => m?.removeEventListener?.("change", cb); },
    () => window.matchMedia?.(query).matches ?? false,
    () => false,
  );
}
