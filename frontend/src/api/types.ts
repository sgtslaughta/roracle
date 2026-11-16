// Shared API types. Shapes follow the backend plans:
//   P1  /me, POST /documents (201 / 409 duplicate / 413), /me/uploads
//   P2  ingest stage names, POST /documents/{id}/versions/{vid}/near-dup
//   P3  /search, /documents/{id}, votes, tags, references, cited-by, collections
// Fields marked "ui:" are additions the UI needs beyond the plan text (flagged in the P5 report).
import type { CommunityNote } from "./notes";

export interface Me { id: string; username: string; groups: string[]; is_admin: boolean }

export type Visibility = "everyone" | "org_unit" | "private" | "shared";

export interface Votes { up: number; down: number; mine: 1 | -1 | 0 }
export const DOWNVOTE_REASONS = ["outdated", "wrong", "low_quality", "superseded"] as const;
export type DownvoteReason = (typeof DOWNVOTE_REASONS)[number];
export interface VoteRequest { value: 1 | -1 | 0; reason?: DownvoteReason; superseded_by?: string }

/** Docling bbox: PDF points. Docling's default origin is BOTTOMLEFT. */
export interface Bbox { l: number; t: number; r: number; b: number; coord_origin: "BOTTOMLEFT" | "TOPLEFT" }

/** `highlight` is server HTML: escaped text with <em> around matches only. Always render via safeHighlight. */
export interface Passage { page: number; chunk_id: string; text: string; highlight?: string; bbox?: Bbox | null }

export interface SearchHit {
  doc_id: string;
  title: string;
  authors: string[];
  year: number | null;
  score: number;
  passages: Passage[];
  tags: string[];
  votes: Votes;
  file_type: string;
  version: number;
  venue: string | null;
  org_unit: string;
  flag: string | null; // always null today; render when present
  cited_by_count: number;
  owner_id: string;
  /** community notes (search with notes=true); absent in older responses */
  notes?: CommunityNote[];
}

export type FacetName = "tags" | "year" | "file_type" | "org_unit" | "collection" | "author";
/** `label` is set for the collection facet only (value is the collection id). */
export interface FacetValue { value: string; count: number; label?: string }
export interface SearchResponse {
  total: number;
  page: number;
  results: SearchHit[];
  facets: Record<FacetName, FacetValue[]>;
}

export interface DocVersion { id: string; version: number; filename: string; uploaded_at: string; is_latest: boolean }
export interface DocumentDetail {
  id: string;
  title: string;
  authors: string[];
  year: number | null;
  venue: string | null;
  org_unit: string;
  visibility: Visibility;
  owner: { id: string; username: string };
  summary: string | null;
  stage: string;
  tags: string[];
  votes: Votes;
  file_type: string;
  versions: DocVersion[];
  page_count: number;
  flag: string | null;
}

export interface DocumentPatch { title?: string; visibility?: Visibility; org_unit?: string; share_users?: string[]; share_groups?: string[] }
export interface TagsResponse { tag: string; tags: string[] }

/** GET /documents/{id}/versions/{n}/docling: page sizes + chunk boxes for passage highlighting. */
export interface DoclingDoc {
  pages: Record<string, { size: { width: number; height: number } }>;
  chunks: { chunk_id: string; page_no: number; bbox: Bbox }[];
}

export interface ReferenceItem { ref_no: number; raw: string; target: { doc_id: string; title: string } | null } // target.title may be ""
export interface CitedByItem { doc_id: string; title: string; authors: string[]; year: number | null; venue?: string }
export interface Collection { id: string; name: string; count: number }
/** PUT|DELETE /collections/{id}/items/{doc} */
export interface CollectionCount { id: string; count: number }

// ---- uploads (P1 Task 6/7, P2) ----
export const STAGES = ["uploaded", "scanned", "parsed", "summarized", "deduped", "chunked", "embedded", "searchable"] as const;
export type Stage = (typeof STAGES)[number] | "review" | "needs_title" | "rejected" | "cancelled";
export type JobStatus = "queued" | "running" | "done" | "failed";

export interface NearDupOf { document_id: string; title: string | null }
export interface UploadItem {
  document_id: string;
  version_id: string;
  title: string | null; // null until set; UI falls back to filename
  filename: string;
  size: number;
  visibility: Visibility;
  status: JobStatus;
  error: string | null;
  uploaded_at: string;
  stage: Stage;
  stage_label: string;
  page_count: number | null;
  near_dup_of: NearDupOf | null;
}
export interface NearDupResult { version_id: string; document_id: string; stage: Stage }
export type TitleResult = NearDupResult;
export interface NeedsTitleItem { document_id: string; version_id: string; filename: string; owner: string; uploaded_at: string }
export interface UploadsResponse { items: UploadItem[]; total: number }
export interface UploadCreated { document_id: string; version_id: string; sha256: string }
export interface DuplicateDetail { code: "duplicate"; document_id: string }
export type NearDupAction = "new_version" | "separate" | "cancel";
