import { useSyncExternalStore } from "react";

export interface OpenOpts { page?: number; chunkId?: string; version?: number }
export interface ReaderTab { docId: string; version?: number; page: number; chunkId?: string }
export interface ReaderState { tabs: ReaderTab[]; active: string | null }

let state: ReaderState = { tabs: [], active: null };
const subs = new Set<() => void>();
const set = (s: ReaderState) => { state = s; subs.forEach((f) => f()); };

export function openDoc(docId: string, opts: OpenOpts = {}) {
  const tab: ReaderTab = { docId, version: opts.version, page: opts.page ?? 1, chunkId: opts.chunkId };
  const tabs = state.tabs.some((t) => t.docId === docId) ? state.tabs.map((t) => (t.docId === docId ? tab : t)) : [...state.tabs, tab];
  set({ tabs, active: docId });
}

export const getReaderState = () => state;
export const useReaderStore = () => useSyncExternalStore((f) => { subs.add(f); return () => subs.delete(f); }, () => state);
export const resetReaderStore = () => set({ tabs: [], active: null });
