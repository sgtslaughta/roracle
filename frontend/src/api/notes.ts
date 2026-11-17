/** P8 contract: reader annotations (notes), threads, votes and moderation. */
import type { Label } from "./types";

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
  /** own label (null = inherits the document's) and max(document, own) */
  label?: Label | null;
  effective_label?: Label;
}

export interface NoteIn {
  label_id?: string;
  kind: NoteKind;
  body: string;
  anchor?: NoteAnchor | null;
  relation?: NoteRelation | null;
  visibility?: NoteVisibility;
  shares?: NoteShare[];
}
export interface NotePatch { body?: string; visibility?: NoteVisibility; shares?: NoteShare[] }

/* ---- evidence (P8 /annotations/{id}/evidence, /evidence/{id}) ---- */
export const STANCES = ["supports", "refutes", "inconclusive"] as const;
export type Stance = (typeof STANCES)[number];
/** pending → clean | infected | failed; unscanned when no scanner is configured. Download only for clean | unscanned. */
export type ScanStatus = "pending" | "clean" | "infected" | "failed" | "unscanned";
export type ReproOutcome = "reproduced" | "failed";
export const EVIDENCE_MAX_BYTES = 1 << 30; // ROR_EVIDENCE_MAX_BYTES default
export const EVIDENCE_ITEMS_MAX = 100;
export const EVIDENCE_PARAMS_MAX = 64 * 1024;
export const EVIDENCE_META_MAX = 256 * 1024;
export const EVIDENCE_DESC_MAX = 5000;
export const REPRO_NOTE_MAX = 2000;

export interface EvidenceItem { name: string; uri?: string | null; sha256?: string | null }
/** Multipart `meta` field: Provenance + stance. Datetimes must carry an offset; uri and uri_sha256 go together. */
export interface EvidenceMeta {
  stance: Stance;
  code: { repo: string; commit: string };
  environment: { image_digest: string };
  inputs: EvidenceItem[];
  outputs: EvidenceItem[];
  parameters: Record<string, unknown>;
  seed: number | string | null;
  agent: { client: string; model: string };
  started_at: string;
  ended_at: string;
  description: string;
  uri: string | null;
  uri_sha256: string | null;
}
export interface Evidence {
  id: string;
  annotation_id: string;
  stance: Stance;
  /** RO-Crate 1.1 JSON-LD; untrusted, read defensively (features/notes/crate.ts) */
  crate: unknown;
  file: { filename: string | null; size: number | null; sha256: string; scan_status: ScanStatus } | null;
  uri: string | null;
  uri_sha256: string | null;
  reproductions: { reproduced: number; failed: number; mine: ReproOutcome | null };
  /** creator user id (no username in the contract) */
  created_by: string;
  created_at: string | null;
}

/** GET /search?notes=true: up to 2 community notes per hit (6 per page), body cut to 500 chars. */
export interface CommunityNote { id: string; kind: NoteKind; body: string; author: { id: string; username: string }; score: number; page: number | null }
