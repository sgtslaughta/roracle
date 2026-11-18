const CLASS: Record<string, string> = { ok: "done", done: "done", running: "running", partial: "review", failed: "failed", queued: "title" };

/** A server enum as a label: "needs_title" → "Needs title", "ok" → "OK". */
const ACRONYMS = /\b(acl|lsh|llm|pdf|ocr|ok)\b/gi;
/** "acl_sync" -> "ACL sync", "ok" -> "OK". */
export const humanize = (s: string) => (s.charAt(0).toUpperCase() + s.slice(1)).replaceAll("_", " ").replace(ACRONYMS, (m) => m.toUpperCase());

/** Status as a coloured pill; the word is always shown so colour is never the only signal. */
export function StatusPill({ status }: { status: string }) {
  return <span className={`pill ${CLASS[status] ?? ""}`}>{humanize(status)}</span>;
}
