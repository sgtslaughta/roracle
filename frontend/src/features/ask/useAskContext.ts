import { createStore } from "@/lib/store";

export interface Quote { doc_id: string; page: number; text: string }
interface Ctx { ignored: string[]; quote: Quote | null }

const ctx = createStore<Ctx>({ ignored: [], quote: null });
const draft = createStore("");

export const useAskContext = () => ctx.use((s) => s);
export const getAskContext = () => ctx.get();
export const toggleIgnored = (id: string) =>
  ctx.set((s) => ({ ...s, ignored: s.ignored.includes(id) ? s.ignored.filter((x) => x !== id) : [...s.ignored, id] }));
export const unignore = (id: string) => ctx.set((s) => (s.ignored.includes(id) ? { ...s, ignored: s.ignored.filter((x) => x !== id) } : s));
export const setQuote = (quote: Quote | null) => ctx.set((s) => ({ ...s, quote }));
export const resetAskContext = () => { ctx.reset(); draft.reset(); wantFocus = false; };

/** The unsent question text lives here so it survives route changes and can be restored after a rejected send. */
export const useDraft = () => draft.use((s) => s);
export const setDraft = (text: string) => draft.set(text);
export const getDraft = () => draft.get();

let el: HTMLTextAreaElement | null = null;
let wantFocus = false;
/** The composer registers its textarea; a focus request made before it mounts is honoured on mount. */
export function registerComposer(node: HTMLTextAreaElement | null) {
  el = node;
  if (node && wantFocus) { wantFocus = false; node.focus(); }
}
export function focusComposer() {
  if (el) el.focus();
  else wantFocus = true;
}
