const CLASS: Record<string, string> = { ok: "done", done: "done", running: "running", partial: "review", failed: "failed", queued: "title" };

/** Status as a coloured pill; the word is always shown so colour is never the only signal. */
export function StatusPill({ status }: { status: string }) {
  return <span className={`pill ${CLASS[status] ?? ""}`}>{status}</span>;
}
