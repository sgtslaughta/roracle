/** Wire types for the admin section (P10 REST contract). Strings from the server are untrusted: render as text. */
export type ConnectorKind = "s3" | "fs" | "http";
export type ConnectorVisibility = "private" | "org_unit" | "everyone";
export type RunStatus = "running" | "ok" | "partial" | "failed";

export interface S3Config { endpoint: string; bucket: string; prefix: string; region: string; access_key?: string | null; secret_key?: string | null }
export interface FsConfig { path: string; include: string[] }
export interface HttpConfig { start_urls: string[]; max_depth: number; max_pages: number; respect_robots: boolean; ingest_html: boolean }
export type ConnectorConfig = S3Config | FsConfig | HttpConfig;

export interface RunStats { seen?: number; ingested?: number; versions?: number; unchanged?: number; duplicate?: number; too_large?: number; errors?: number }
export interface ConnectorRun { id: number; status: RunStatus; started_at: string; finished_at: string | null; stats: RunStats | null }
export interface ConnectorRunDetail extends ConnectorRun { log: string | null }

export interface Connector {
  id: string; name: string; kind: ConnectorKind; config: Record<string, unknown>;
  visibility: ConnectorVisibility; org_unit: string; schedule_minutes: number | null; enabled: boolean;
  next_run_at: string | null; last_run_at: string | null; created_at: string; last_run: ConnectorRun | null;
}
/** POST body; PATCH takes the same fields (all optional, `kind` omitted). */
export interface ConnectorBody {
  name: string; kind?: ConnectorKind; config: Record<string, unknown>;
  visibility: ConnectorVisibility; org_unit: string; enabled: boolean;
  /** omitted on edit unless changed: PATCHing it resets the next run to "due now" */
  schedule_minutes?: number | null;
}

export interface Keyset<T> { items: T[]; next_before_id: number | null }
export interface Paged<T> { items: T[]; total: number }

export type JobStatus = "queued" | "running" | "done" | "failed";
export interface AdminJob {
  id: number; kind: string; status: JobStatus; attempts: number; last_error: string | null;
  payload: Record<string, unknown>; created_at: string; updated_at: string; document_id?: string; filename?: string;
}
export interface JobsSummary { jobs: Record<string, Partial<Record<JobStatus, number>>>; versions: Record<string, number> }

export type ReviewSource = "all" | "upload" | "connector";
export interface ReviewItem {
  document_id: string; version_id: string; filename: string; owner: string; connector_id: string | null;
  uploaded_at: string; match: { document_id: string; title: string } | null;
}

export type SettingValue = boolean | number | string | string[];
export type SettingsMap = Record<string, { value: SettingValue; source: "env" | "override" }>;
export interface TagCount { tag: string; documents: number }
export interface AuditEntry { id: number; at: string; user_id: string | null; username: string | null; action: string; target: string; client: string | null; detail: unknown }
export interface AdminUser { id: string; username: string; email: string | null; groups: string[]; is_admin: boolean; created_at: string; last_login_at: string | null; documents: number; bytes: number }
export interface GroupCount { group: string; members: number }
export interface AdminStats {
  documents: { live: number; deleted: number }; versions: Record<string, number>; blobs: { count: number; bytes: number };
  users: number; chats: number; connectors: number; top_uploaders: { user_id: string; username: string; documents: number; bytes: number }[];
}
export interface AdminHealth {
  status: "ok" | "degraded"; checks: Record<string, string>;
  queue: { queued: number; running: number; failed: number; oldest_queued_s: number | null } | null; scheduler: { tick_queued: boolean } | null;
}
