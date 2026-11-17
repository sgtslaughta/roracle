import type { QueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import type { ChatDetail, Citation, ExcludedByLabel, Viewer } from "@/api/chat";
import { createStore } from "@/lib/store";
import { streamMessage } from "./stream";
import { getDraft, setDraft, setQuote } from "./useAskContext";
import { isSettled } from "./turns";

export interface PendingTurn {
  question: string;
  selection: Viewer["selection"];
  /** number of server messages when the turn started */
  base: number;
  text: string;
  citations: Citation[];
  excluded?: ExcludedByLabel[];
  /** status line while the library is searched (agentic retrieval), cleared by the first token */
  progress?: string;
  /** "Summarizing <title> (v<n>)": which paper a whole-document summary reads */
  note?: string;
  status: "streaming" | "done" | "interrupted" | "failed";
  detail?: string;
  messageId?: string;
}

/** In-flight turns by chat id. They live outside React so a turn survives the /ask -> /ask/:id navigation. */
const turns = createStore<Record<string, PendingTurn>>({});
/** Chats the server said no longer exist (404 on send). */
const gone = createStore<Record<string, true>>({});
export const useChatGone = (chatId: string | undefined) => gone.use((s) => !!chatId && !!s[chatId]);
/** message id -> context withheld by label (the server does not store it, so it is kept for the session). */
export const excludedByMessage = new Map<string, ExcludedByLabel[]>();
export const noteByMessage = new Map<string, string>();
const ctrls = new Map<string, AbortController>();
const viewers = new Map<string, number>();

export const usePendingTurn = (chatId: string | undefined) => turns.use((s) => (chatId ? s[chatId] : undefined));
export const resetAskTurns = () => { ctrls.forEach((c) => c.abort()); ctrls.clear(); viewers.clear(); excludedByMessage.clear(); noteByMessage.clear(); turns.reset(); gone.reset(); };

const put = (id: string, p: PendingTurn | null) =>
  turns.set((s) => {
    const { [id]: _old, ...rest } = s;
    return p ? { ...s, [id]: p } : rest;
  });
const patch = (id: string, f: (p: PendingTurn) => PendingTurn) => turns.set((s) => (s[id] ? { ...s, [id]: f(s[id]) } : s));

export const stopTurn = (chatId: string) => ctrls.get(chatId)?.abort();

/** A page showing `chatId` calls this on mount; when the last one goes away the in-flight answer is aborted. */
export function attachChat(chatId: string) {
  viewers.set(chatId, (viewers.get(chatId) ?? 0) + 1);
  return () => {
    viewers.set(chatId, (viewers.get(chatId) ?? 1) - 1);
    // deferred: a route change unmounts the old page and mounts the new one in the same commit
    setTimeout(() => { if (!viewers.get(chatId)) ctrls.get(chatId)?.abort(); }, 0);
  };
}

export interface StartOpts { question: string; viewer: Viewer; model?: string }

export function startTurn(qc: QueryClient, chatId: string, { question, viewer, model }: StartOpts) {
  ctrls.get(chatId)?.abort();
  const ctrl = new AbortController();
  ctrls.set(chatId, ctrl);
  const base = qc.getQueryData<ChatDetail>(["chat", chatId])?.messages.length ?? 0;
  put(chatId, { question, selection: viewer.selection, base, text: "", citations: [], status: "streaming" });

  let buf = "";
  let timer: ReturnType<typeof setTimeout> | number = 0;
  const flush = () => {
    if (timer) { (typeof cancelAnimationFrame === "function" ? cancelAnimationFrame : clearTimeout)(timer as number); timer = 0; }
    if (!buf) return;
    const t = buf;
    buf = "";
    patch(chatId, (p) => ({ ...p, text: p.text + t }));
  };
  const schedule = () => { if (!timer) timer = typeof requestAnimationFrame === "function" ? requestAnimationFrame(flush) : setTimeout(flush, 16); };
  const stale = () => ctrls.get(chatId) !== ctrl; // a newer turn took over this chat
  const finish = (fields: Partial<PendingTurn>) => {
    if (stale()) return;
    flush();
    patch(chatId, (p) => ({ ...p, ...fields }));
    void Promise.all([qc.invalidateQueries({ queryKey: ["chat", chatId] }), qc.invalidateQueries({ queryKey: ["chats"] })]).then(() => {
      const cur = turns.get()[chatId];
      const msgs = qc.getQueryData<ChatDetail>(["chat", chatId])?.messages;
      const newer = ctrls.get(chatId) && ctrls.get(chatId) !== ctrl;
      if (!cur || !msgs || newer) return;
      if (isSettled(cur, msgs)) put(chatId, null);
      else if (cur.status === "interrupted" && msgs.length === cur.base) {
        // aborted before the server saved the question: drop the local turn and give the question back
        put(chatId, null);
        if (!getDraft()) { setDraft(cur.question); setQuote(cur.selection); }
      }
    });
  };

  streamMessage(chatId, { content: question, viewer, model }, {
    onToken: (t) => { buf += t; schedule(); },
    onProgress: (g) => patch(chatId, (p) => ({ ...p, progress: `Searching sub-question ${Math.min(g.done + 1, g.of)}/${g.of} (round ${g.step})…` })),
    onCitations: (items) => { flush(); patch(chatId, (p) => ({ ...p, citations: items })); },
    onReading: (part, of) => patch(chatId, (p) => ({ ...p, progress: `Reading part ${part} of ${of}…` })),
    onSummarizing: (s) => patch(chatId, (p) => ({ ...p, note: `Summarizing ${s.title} (v${s.version})${s.partial ? `: read ${s.partial.read} of ${s.partial.of} parts, so the summary is partial` : ""}` })),
    onExcluded: (items) => patch(chatId, (p) => ({ ...p, excluded: items })),
    onDone: (messageId) => {
      const ex = turns.get()[chatId]?.excluded;
      if (ex?.length) excludedByMessage.set(messageId, ex);
      const note = turns.get()[chatId]?.note;
      if (note) noteByMessage.set(messageId, note);
      finish({ status: "done", messageId });
    },
    onError: (detail) => finish({ status: "failed", detail }),
  }, ctrl.signal).catch((e: unknown) => {
    if (stale()) return;
    if (ctrl.signal.aborted) return finish({ status: "interrupted" });
    if (e instanceof ApiError) { // rejected before streaming: nothing was saved
      put(chatId, null);
      if (e.status === 404) gone.set((s) => ({ ...s, [chatId]: true }));
      else if (e.status === 403) { toast("This chat is read-only."); void qc.invalidateQueries({ queryKey: ["chat", chatId] }); }
      else toast(e.status === 503 ? "Answering is unavailable right now. Try again shortly." : e.message);
      setDraft(question);
      setQuote(viewer.selection);
      return;
    }
    finish({ status: "failed", detail: "The connection was lost." });
  }).finally(() => { if (ctrls.get(chatId) === ctrl) ctrls.delete(chatId); });
}
