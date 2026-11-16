import type { Connector, ConnectorBody, ConnectorKind, ConnectorVisibility } from "@/api/admin";

/** What the API shows in place of a stored secret; sending it back keeps the secret. */
export const MASK = "***";

export interface FormState {
  name: string; kind: ConnectorKind; visibility: ConnectorVisibility; org_unit: string; schedule: string; enabled: boolean;
  endpoint: string; bucket: string; prefix: string; region: string; access_key: string; secret_key: string;
  path: string; include: string[];
  start_urls: string[]; max_depth: string; max_pages: string; respect_robots: boolean; ingest_html: boolean;
}

const s = (v: unknown, d = "") => (typeof v === "string" ? v : d);
const list = (v: unknown, d: string[]) => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : d);

export function initialForm(c?: Connector): FormState {
  const k = c?.config ?? {};
  return {
    name: c?.name ?? "", kind: c?.kind ?? "s3", visibility: c?.visibility ?? "private", org_unit: c?.org_unit ?? "",
    schedule: c?.schedule_minutes ? String(c.schedule_minutes) : "", enabled: c?.enabled ?? true,
    endpoint: s(k.endpoint), bucket: s(k.bucket), prefix: s(k.prefix), region: s(k.region, "us-east-1"), access_key: s(k.access_key), secret_key: s(k.secret_key),
    path: s(k.path), include: list(k.include, ["*"]),
    start_urls: list(k.start_urls, []), max_depth: String(k.max_depth ?? 1), max_pages: String(k.max_pages ?? 100),
    respect_robots: k.respect_robots !== false, ingest_html: k.ingest_html === true,
  };
}

const intIn = (v: string, lo: number, hi: number) => /^\d+$/.test(v.trim()) && Number(v) >= lo && Number(v) <= hi;
export const isHttpUrl = (v: string) => /^https?:\/\/\S+$/i.test(v);

/**
 * Request body from the form, or an error message. `editing` sends cleared optional fields as null
 * (PATCH deletes the key) and keeps an untouched secret as "***".
 */
export function buildBody(f: FormState, editing: boolean, lists: { include: string[]; start_urls: string[] }): { body: ConnectorBody } | { error: string } {
  const name = f.name.trim();
  if (!name) return { error: "Give the connector a name." };
  const org_unit = f.visibility === "org_unit" ? f.org_unit.trim() : "";
  if (f.visibility === "org_unit" && !org_unit.startsWith("/")) return { error: "Org unit must start with “/”, for example /Mathematics." };
  const schedule = f.schedule.trim();
  if (schedule && !intIn(schedule, 5, 10080)) return { error: "Schedule must be between 5 and 10080 minutes, or empty for manual runs." };
  let config: Record<string, unknown>;
  if (f.kind === "s3") {
    if (!/^https?:\/\/\S+$/i.test(f.endpoint.trim())) return { error: "Endpoint must be an http(s) URL." };
    if (!f.bucket.trim()) return { error: "Bucket is required." };
    const opt = (v: string) => (v.trim() ? v.trim() : editing ? null : undefined);
    config = {
      endpoint: f.endpoint.trim(), bucket: f.bucket.trim(), prefix: f.prefix.trim(), region: f.region.trim() || "us-east-1",
      access_key: opt(f.access_key), secret_key: f.secret_key ? f.secret_key : editing ? null : undefined,
    };
    config = Object.fromEntries(Object.entries(config).filter(([, v]) => v !== undefined));
  } else if (f.kind === "fs") {
    if (!f.path.trim()) return { error: "Path is required." };
    if (!lists.include.length) return { error: "Add at least one include pattern, for example *.pdf." };
    config = { path: f.path.trim(), include: lists.include };
  } else {
    if (!lists.start_urls.length) return { error: "Add at least one start URL." };
    if (!intIn(f.max_depth, 0, 3)) return { error: "Max depth must be 0 to 3." };
    if (!intIn(f.max_pages, 1, 1000)) return { error: "Max pages must be 1 to 1000." };
    config = { start_urls: lists.start_urls, max_depth: Number(f.max_depth), max_pages: Number(f.max_pages), respect_robots: f.respect_robots, ingest_html: f.ingest_html };
  }
  return { body: { name, ...(editing ? {} : { kind: f.kind }), config, visibility: f.visibility, org_unit, schedule_minutes: schedule ? Number(schedule) : null, enabled: f.enabled } };
}
