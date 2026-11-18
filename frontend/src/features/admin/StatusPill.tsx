const CLASS: Record<string, string> = { ok: "done", done: "done", running: "running", partial: "review", failed: "failed", queued: "title" };

/** A server enum as a label: "needs_title" → "Needs title", "ok" → "OK". */
export const humanize = (s: string) => (s === "ok" ? "OK" : (s.charAt(0).toUpperCase() + s.slice(1)).replaceAll("_", " "));

/** Status as a coloured pill; the word is always shown so colour is never the only signal. */
export function StatusPill({ status }: { status: string }) {
  return <span className={`pill ${CLASS[status] ?? ""}`}>{humanize(status)}</span>;
}
