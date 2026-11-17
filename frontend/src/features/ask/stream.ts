import { authedFetch, errorOf, seg } from "@/api/client";
import type { Citation, ExcludedByLabel, SendMessageBody } from "@/api/chat";

export interface SseEvent { event: string; data: string }

/** Incremental SSE parser (WHATWG rules): feed text chunks as they arrive. */
export function createSseParser(onEvent: (ev: SseEvent) => void): (chunk: string) => void {
  let buf = "";
  let event = "";
  let data: string[] = [];
  const line = (l: string) => {
    if (l === "") {
      if (data.length) onEvent({ event: event || "message", data: data.join("\n") });
      event = ""; data = [];
      return;
    }
    if (l[0] === ":") return;
    const i = l.indexOf(":");
    const field = i < 0 ? l : l.slice(0, i);
    let value = i < 0 ? "" : l.slice(i + 1);
    if (value[0] === " ") value = value.slice(1);
    if (field === "event") event = value;
    else if (field === "data") data.push(value);
  };
  return (chunk) => {
    buf += chunk;
    // a trailing \r may be the first half of \r\n: hold it back until the next chunk
    const hold = buf.endsWith("\r") ? 1 : 0;
    const parts = buf.slice(0, buf.length - hold).split(/\r\n|\n|\r/);
    buf = parts.pop()! + buf.slice(buf.length - hold);
    parts.forEach(line);
    if (hold && buf === "\r") { buf = ""; line(""); } // lone trailing CR: line end (blank-line check needs next chunk otherwise)
  };
}

export interface StreamHandlers {
  onToken(text: string): void;
  onCitations(items: Citation[]): void;
  onExcluded?(items: ExcludedByLabel[]): void;
  /** agentic retrieval: sub-question progress (round `step`, `done` of `of` sub-questions searched) */
  onProgress?(p: { step: number; of: number; done: number }): void;
  /** summary of one document: map-reduce part `part` of `of` is being read */
  onReading?(part: number, of: number): void;
  /** the open paper being summarized (shown as a note in the turn) */
  onSummarizing?(s: { title: string; version: number; partial?: { read: number; of: number } }): void;
  onDone(messageId: string): void;
  onError(detail: string): void;
}

/** POST a question and dispatch the SSE answer. Rejects on non-OK (ApiError) and on abort. */
export async function streamMessage(chatId: string, body: SendMessageBody, h: StreamHandlers, signal?: AbortSignal): Promise<void> {
  const res = await authedFetch(`/chats/${seg(chatId)}/messages`, { method: "POST", json: body, headers: { Accept: "text/event-stream" }, signal });
  if (!res.ok) throw await errorOf(res);
  if (!res.body) { h.onError("The answer was cut off."); return; }
  let finished = false;
  const parse = createSseParser(({ event, data }) => {
    if (finished || signal?.aborted) return;
    let p: Record<string, unknown>;
    try { p = JSON.parse(data) as Record<string, unknown>; } catch { return; }
    if (event === "token") h.onToken(String(p.text ?? ""));
    else if (event === "citations") { if (Array.isArray(p.items)) h.onCitations(p.items as Citation[]); }
    else if (event === "progress") { if (typeof p.step === "number" && typeof p.of === "number") h.onProgress?.({ step: p.step, of: p.of, done: Number(p.done ?? 0) }); }
    else if (event === "reading") { if (typeof p.part === "number" && typeof p.of === "number") h.onReading?.(p.part, p.of); }
    else if (event === "meta") {
      if (Array.isArray(p.excluded_by_label)) h.onExcluded?.(p.excluded_by_label as ExcludedByLabel[]);
      const s = p.summarize as { title?: unknown; version?: unknown; partial?: { read?: unknown; of?: unknown } } | undefined;
      const pt = s?.partial && typeof s.partial.read === "number" && typeof s.partial.of === "number" ? { read: s.partial.read, of: s.partial.of } : undefined;
      if (s && typeof s.title === "string") h.onSummarizing?.({ title: s.title, version: Number(s.version ?? 1), partial: pt });
    }
    else if (event === "done") { if (typeof p.message_id === "string") { finished = true; h.onDone(p.message_id); } }
    else if (event === "error") { finished = true; h.onError(p.detail === "inference failed" ? "The model couldn't finish this answer." : String(p.detail ?? "Something went wrong.")); }
  });
  const reader = res.body.getReader();
  const dec = new TextDecoder(); // also strips a leading BOM from the first chunk
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      parse(dec.decode(value, { stream: true }));
      signal?.throwIfAborted();
    }
    parse(dec.decode());
  } finally {
    await reader.cancel().catch(() => {});
    try { reader.releaseLock(); } catch { /* already released */ }
  }
  if (!finished) h.onError("The answer was cut off.");
}
