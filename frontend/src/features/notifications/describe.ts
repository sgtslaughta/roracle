import { isDocId, isUuid } from "@/lib/ids";

/** P10 notification kinds and their payloads (untrusted: read defensively, render as plain text). */
export interface AppNotification { id: number; kind: string; payload: Record<string, unknown>; created_at: string; read_at: string | null }
/** `note`: open this note in the reader's Notes tab instead of following `to`. */
export interface Described { text: string; to?: string; note?: { docId: string; noteId: string } }

const MAX = 120;
/** A payload string, trimmed to one line; anything else is "". */
const str = (v: unknown) => (typeof v === "string" || typeof v === "number" ? String(v).replace(/\s+/g, " ").trim().slice(0, MAX) : "");
const q = (v: unknown, fallback: string) => (str(v) ? `“${str(v)}”` : fallback);
/** Internal links only, built from a validated id, never from a payload URL. */
const docLink = (id: unknown) => (isDocId(id) ? `/d/${id}` : undefined);

const draftLink = (id: unknown): Omit<Described, "text"> => (isDocId(id) ? { to: `/drafts/${id}` } : { to: "/drafts" });
const note = (v: unknown) => (str(v) ? ` Note: ${str(v)}` : "");

const noteLink = (docId: unknown, noteId: unknown): Omit<Described, "text"> =>
  isDocId(docId) ? { to: `/d/${docId}`, ...(isDocId(noteId) && { note: { docId, noteId } }) } : {};

/** One plain sentence and an optional in-app link for a notification. Unknown kinds get a generic sentence. */
export function describe(kind: string, payload: Record<string, unknown> | null | undefined): Described {
  const p = payload && typeof payload === "object" ? payload : {};
  switch (kind) {
    case "upload.searchable":
      return { text: `${q(p.title || p.filename, "Your upload")} is ready to search.`, to: docLink(p.document_id) ?? "/uploads" };
    case "upload.near_dup":
      return { text: `${q(p.filename, "Your upload")} looks like ${q(p.match_title, "a document already in the library")}. Choose whether it's a new version.`, to: "/uploads" };
    case "upload.failed":
      return { text: `${q(p.filename, "Your upload")} couldn't be processed${str(p.error) ? `: ${str(p.error)}` : ""}.`, to: "/uploads" };
    case "ops.job_failed":
      return { text: `A ${str(p.kind) || "background"} job failed${str(p.error) ? `: ${str(p.error)}` : ""}.`, to: "/admin/jobs" };
    case "ops.connector_run": {
      const s = str(p.status);
      return { text: `${str(p.name) ? `Connector ${q(p.name, "")}` : "A connector run"} finished${s ? `: ${s}` : ""}.`, to: isUuid(p.connector_id) ? `/admin/connectors/${p.connector_id}` : "/admin/connectors" };
    }
    case "proposal.new":
      return { text: `${str(p.client) || "An agent"} proposed a note.`, to: "/proposals" };
    case "note.reply":
      return { text: "Someone replied to your note.", ...noteLink(p.document_id, p.reply_id) };
    case "note.on_document":
      return { text: "Someone added a note to your document.", ...noteLink(p.document_id, p.note_id) };
    case "draft.review_requested":
      return { text: `${q(p.title, "A draft")} is waiting for your review.`, ...draftLink(p.draft_id) };
    case "draft.approved":
      return { text: `${q(p.title, "Your draft")} was approved and published.${note(p.note)}`, ...draftLink(p.draft_id) };
    case "draft.rejected":
      return { text: `${q(p.title, "Your draft")} was sent back.${note(p.note)}`, ...draftLink(p.draft_id) };
    default:
      return { text: "Something changed." };
  }
}
