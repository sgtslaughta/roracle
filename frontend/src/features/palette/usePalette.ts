import { useSyncExternalStore } from "react";

let open = false;
const subs = new Set<() => void>();
const set = (v: boolean) => { open = v; subs.forEach((f) => f()); };

export const openPalette = () => set(true);
export const closePalette = () => set(false);
export const togglePalette = () => set(!open);
export const usePaletteOpen = () => useSyncExternalStore((f) => { subs.add(f); return () => subs.delete(f); }, () => open);
