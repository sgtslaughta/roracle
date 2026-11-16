/** P8 contract: reader annotations (notes), threads, votes and moderation. */

export const NOTE_KINDS = ["comment", "finding", "correction", "question", "relation"] as const;
export type NoteKind = (typeof NOTE_KINDS)[number];
export const RELATION_TYPES = ["extends", "contradicts", "replicates", "supersedes"] as const;
export type RelationType = (typeof RELATION_TYPES)[number];
export type NoteVisibility = "private" | "shared" | "everyone";
export const REPORT_REASONS = ["spam", "abusive", "wrong", "other"] as const;
export type ReportReason = (typeof REPORT_REASONS)[number];

export const NOTE_BODY_MAX = 10_000;
export const QUOTE_MAX = 1000;
export const NOTE_SHARES_MAX = 50;
export const PRINCIPAL_MAX = 500;

export interface NoteShare { kind: "user" | "group"; principal: string }
/** Anchor box: the backend copies the docling chunk box as x0=l, y0=t, x1=r, y1=b (see notes/anchor.ts). */
export interface NoteBbox { x0: number; y0: number; x1: number; y1: number; coord_origin?: string | null }
export interface NoteAnchor { quote: string; prefix?: string; suffix?: string; page?: number | null; bbox?: NoteBbox | null }
export interface NoteRelation { type: RelationType; target_document_id: string }
export interface NoteVotes { up: number; down: number; mine: 1 | -1 | 0; score: number }
export interface EvidenceCounts { supports: number; refutes: number; inconclusive: number }

export interface Note {
  id: string;
  document_id: string;
  version_id: string;
  version: number | null;
  parent_id: string | null;
  author: { id: string; username: string };
  kind: NoteKind;
  body: string;
  anchor: NoteAnchor | null;
  relation: NoteRelation | null;
  visibility: NoteVisibility;
  /** author only; empty for everyone else */
  shares: NoteShare[];
  status: "visible" | "hidden" | "removed";
  agent_assisted: boolean;
  /** latest version number the anchor could not be found in; null when it applies */
  may_not_apply_to_version: number | null;
  votes: NoteVotes;
  evidence: EvidenceCounts;
  created_at: string;
  updated_at: string;
}

export interface NoteIn {
  kind: NoteKind;
  body: string;
  anchor?: NoteAnchor | null;
  relation?: NoteRelation | null;
  visibility?: NoteVisibility;
  shares?: NoteShare[];
}
export interface NotePatch { body?: string; visibility?: NoteVisibility; shares?: NoteShare[] }
