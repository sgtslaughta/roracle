import { createStore } from "@/lib/store";

interface HelpState { open: boolean; topic: string | null; section: string | null }

export const helpStore = createStore<HelpState>({ open: false, topic: null, section: null });

export const openHelp = (topic?: string, section?: string) => helpStore.set({ open: true, topic: topic ?? null, section: section ?? null });
export const closeHelp = () => helpStore.set((s) => ({ ...s, open: false }));
export const useHelp = () => helpStore.use((s) => s);
