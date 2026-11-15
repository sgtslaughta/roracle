import { useSyncExternalStore } from "react";
import type { Bbox } from "@/api/types";

export type Focus = "split" | "three-quarter" | "full";
export type ReaderView = "doc" | "refs" | "cited" | "notes";
export const FOCUS_KEY = "roracle-reader-focus";
const FOCUS_ORDER: Focus[] = ["split", "three-quarter", "full"];
export const MAX_TABS = 3;
export const MIN_WIDTH = 380;

export interface OpenOpts { page?: number; chunkId?: string; version?: number; bbox?: Bbox | null }
export interface ReaderTab { docId: string; version?: number; page: number; chunkId?: string; bbox?: Bbox | null; seq: number }
export interface ReaderState { tabs: ReaderTab[]; active: string | null; view: ReaderView; focus: Focus; width: number }

function loadFocus(): Focus {
  try {
    const v = localStorage.getItem(FOCUS_KEY);
    return v === "three-quarter" || v === "full" ? v : "split";
  } catch {
    return "split";
  }
}

let state: ReaderState = { tabs: [], active: null, view: "doc", focus: loadFocus(), width: 620 };
let seq = 0;
const subs = new Set<() => void>();
const set = (patch: Partial<ReaderState>) => { state = { ...state, ...patch }; subs.forEach((f) => f()); };

export function openDoc(docId: string, opts: OpenOpts = {}) {
  const tab: ReaderTab = { docId, version: opts.version, page: opts.page ?? 1, chunkId: opts.chunkId, bbox: opts.bbox, seq: ++seq };
  let tabs = state.tabs.some((t) => t.docId === docId) ? state.tabs.map((t) => (t.docId === docId ? tab : t)) : [...state.tabs, tab];
  if (tabs.length > MAX_TABS) tabs = tabs.slice(tabs.length - MAX_TABS); // oldest evicted
  set({ tabs, active: docId, view: "doc" });
}

export function closeTab(docId: string) {
  const tabs = state.tabs.filter((t) => t.docId !== docId);
  set({ tabs, active: state.active === docId ? (tabs.at(-1)?.docId ?? null) : state.active });
}
/** Closing the reader keeps the remembered focus level and everything outside the reader. */
export const closeReader = () => set({ tabs: [], active: null, view: "doc" });
export const setActive = (docId: string) => set({ active: docId, view: "doc" });
export const setView = (view: ReaderView) => set({ view });
export const setVersion = (docId: string, version: number) =>
  set({ tabs: state.tabs.map((t) => (t.docId === docId ? { ...t, version, page: 1, chunkId: undefined, bbox: null, seq: ++seq } : t)) });
export const setWidth = (width: number) => set({ width });

function setFocus(focus: Focus) {
  try { localStorage.setItem(FOCUS_KEY, focus); } catch { /* storage unavailable */ }
  set({ focus });
}
export const cycleFocus = () => setFocus(FOCUS_ORDER[(FOCUS_ORDER.indexOf(state.focus) + 1) % FOCUS_ORDER.length]);

/** Esc: full -> three-quarter -> split -> closed. Returns false when there was nothing to step back. */
export function stepBack(): boolean {
  if (!state.tabs.length) return false;
  if (state.focus === "full") setFocus("three-quarter");
  else if (state.focus === "three-quarter") setFocus("split");
  else closeReader();
  return true;
}

export const getReaderState = () => state;
export const useReaderStore = () => useSyncExternalStore((f) => { subs.add(f); return () => subs.delete(f); }, () => state);
export function resetReaderStore() { state = { tabs: [], active: null, view: "doc", focus: loadFocus(), width: 620 }; subs.forEach((f) => f()); }
