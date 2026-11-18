// P9 drafts contract (.superpowers/sdd/polish-web/drafts-contract.md)
import type { Label, Visibility } from "./types";

export interface Who { id: string; username: string }
export type DraftStatus = "draft" | "in_review" | "submitted";
export interface DraftSummary {
  id: string; title: string; profile: string | null; status: DraftStatus; owner: Who;
  coauthors: { users: string[]; groups: string[] }; latest_version: number; updated_at: string; review_note: string | null; published: boolean; label: Label;
}
export interface VersionRef { version: number; author: Who; created_at: string }
export interface DraftDetail extends DraftSummary { content: string; versions: VersionRef[] }
export interface VersionBody extends VersionRef { content: string }
export interface Finding { rule: string; message: string; line: number | null }
export interface Validation {
  version: number; profile: { name: string; version: number; source: "builtin" | "db" }; checked_by: string; ok: boolean;
  blocking: Finding[]; warnings: Finding[]; style_unavailable?: true;
}
export interface CiteItem {
  key: string; sentence: string; sentence_hash: string; status: "resolved" | "unresolved";
  verdict?: "supported" | "unsupported" | "unclear" | null; reason?: string; chunk_id?: string | null; doc_id?: string | null; title?: string | null; acknowledged: boolean;
}
export interface Citations { version: number; checked_by: string; ok: boolean; items: CiteItem[] }
export interface SubmitBody { visibility: Visibility; share_users: string[]; share_groups: string[]; label_id?: string }
export interface SubmitResult { status: "submitted" | "in_review"; version: number; document_version?: number }

export const BUILTIN_PROFILES = ["dissertation", "journal-article", "conference-paper", "technical-report", "grant-proposal", "literature-review", "preregistration"];
export const TITLE_MAX = 500;
