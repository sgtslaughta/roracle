import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { API_BASE, ApiError, apiFetch, auth, authedFetch, errorOf, seg } from "@/api/client";
import type { Evidence, EvidenceMeta, ReproOutcome } from "@/api/notes";

const key = (noteId: string) => ["evidence", noteId];

export const SCAN_POLL_MS = 5000;
const SCAN_POLL_MAX = 60; // ~5 min, then a reload picks it up

/** Polls (bounded) while any file is still being scanned, so Download appears once it turns clean. */
export const useEvidence = (noteId: string, enabled = true) =>
  useQuery({
    queryKey: key(noteId), enabled,
    queryFn: async () => (await apiFetch<{ items: Evidence[] }>(`/annotations/${seg(noteId)}/evidence`)).items,
    refetchInterval: (q) =>
      q.state.data?.some((e) => e.file?.scan_status === "pending") && q.state.dataUpdateCount <= SCAN_POLL_MAX ? SCAN_POLL_MS : false,
  });

/** Evidence writes change the note's evidence counts too, so the document's notes list is refreshed as well. */
function useEvidenceMutation<V, R>(noteId: string, docId: string, fn: (v: V) => Promise<R>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSettled: () => Promise.all([qc.invalidateQueries({ queryKey: key(noteId) }), qc.invalidateQueries({ queryKey: ["notes", docId] }), qc.invalidateQueries({ queryKey: ["search"] })]),
  });
}

export const useReproduce = (noteId: string, docId: string) =>
  useEvidenceMutation(noteId, docId, ({ id, outcome, note }: { id: string; outcome: ReproOutcome | null; note: string }) =>
    apiFetch<Evidence>(`/evidence/${seg(id)}/reproduction`, { method: "PUT", json: { outcome, note } }));
export const useDeleteEvidence = (noteId: string, docId: string) =>
  useEvidenceMutation(noteId, docId, (id: string) => apiFetch<void>(`/evidence/${seg(id)}`, { method: "DELETE" }));
export function useEvidenceAdded(noteId: string, docId: string) {
  const qc = useQueryClient();
  return () => Promise.all([qc.invalidateQueries({ queryKey: key(noteId) }), qc.invalidateQueries({ queryKey: ["notes", docId] }), qc.invalidateQueries({ queryKey: ["search"] })]);
}

/** Mirror of the backend's safe_filename: last path part, no control chars or quotes, no leading/trailing dots. */
export function safeFilename(name: string | null | undefined): string {
  const base = (name ?? "").split(/[\\/]/).pop()!.replace(/[\x00-\x1f\x7f"]/g, "").replace(/^[. ]+|[. ]+$/g, "").slice(0, 200); // eslint-disable-line no-control-regex
  return base || "evidence";
}

export const canDownload = (e: Evidence) => !!e.file && (e.file.scan_status === "clean" || e.file.scan_status === "unscanned");

/** Fetch the file as a blob and save it through a temporary <a download>: never opened, previewed or navigated to. */
export async function downloadEvidence(e: Evidence): Promise<void> {
  const res = await authedFetch(`/evidence/${seg(e.id)}/file`);
  if (!res.ok) throw await errorOf(res);
  const blob = await res.blob();
  // re-type as octet-stream so no browser treats it as something to render
  const href = URL.createObjectURL(new Blob([blob], { type: "application/octet-stream" }));
  const a = document.createElement("a");
  a.href = href;
  a.download = safeFilename(e.file?.filename);
  a.rel = "noopener";
  document.body.append(a);
  try { a.click(); } finally { a.remove(); setTimeout(() => URL.revokeObjectURL(href), 1000); }
}

export interface Upload { done: Promise<Evidence>; abort: () => void }
export class UploadAborted extends Error { constructor() { super("Upload cancelled"); this.name = "UploadAborted"; } }

/** Multipart POST via XMLHttpRequest (fetch has no upload progress). Bearer token as apiFetch; one silent renew on 401, then sign-in. */
export function uploadEvidence(noteId: string, meta: EvidenceMeta, file: File | null, onProgress: (sent: number, total: number) => void): Upload {
  let xhr: XMLHttpRequest | null = null;
  let aborted = false;
  const form = new FormData();
  form.append("meta", JSON.stringify(meta));
  if (file) form.append("file", file);

  const once = (token: string | null) => new Promise<XMLHttpRequest>((resolve, reject) => {
    if (aborted) return reject(new UploadAborted());
    const x = (xhr = new XMLHttpRequest());
    x.open("POST", `${API_BASE}/annotations/${seg(noteId)}/evidence`);
    if (token) x.setRequestHeader("Authorization", `Bearer ${token}`);
    x.upload.onprogress = (ev) => { if (ev.lengthComputable) onProgress(ev.loaded, ev.total); };
    x.onload = () => resolve(x);
    x.onerror = () => reject(new ApiError(0, "Network error. Check your connection and try again."));
    x.onabort = () => reject(new UploadAborted());
    x.send(form);
  });

  const done = (async () => {
    let x = await once(await auth.token());
    if (x.status === 401) {
      if (await auth.renew()) x = await once(await auth.token());
      if (x.status === 401) await auth.signIn();
    }
    let body: unknown = null;
    try { body = JSON.parse(x.responseText); } catch { /* no JSON body */ }
    if (x.status < 200 || x.status >= 300) throw new ApiError(x.status, (body as { detail?: unknown } | null)?.detail ?? x.statusText);
    return body as Evidence;
  })();
  return { done, abort: () => { aborted = true; xhr?.abort(); } };
}

/* ---- RO-Crate view: the crate is untrusted JSON, so every field is read defensively and rendered as text ---- */
export interface CrateView {
  repo: string; commit: string; image: string; client: string; model: string; seed: string;
  started: string; ended: string; description: string;
  inputs: { name: string; uri: string; sha256: string }[]; outputs: { name: string; uri: string; sha256: string }[];
  parameters: unknown;
}
type Obj = Record<string, unknown>;
const obj = (v: unknown): Obj => (v && typeof v === "object" && !Array.isArray(v) ? (v as Obj) : {});
const str = (v: unknown) => (typeof v === "string" || typeof v === "number" ? String(v) : "");
const items = (v: unknown) => (Array.isArray(v) ? v.map(obj).map((i) => ({ name: str(i.name), uri: str(i.url), sha256: str(i.sha256) })) : []);

export function crateView(crate: unknown): CrateView {
  const graph = Array.isArray(obj(crate)["@graph"]) ? (obj(crate)["@graph"] as unknown[]).map(obj) : [];
  const node = (id: string) => graph.find((n) => n["@id"] === id) ?? {};
  const root = node("./"), run = node("#run"), code = node("#code"), agent = node("#agent");
  return {
    repo: str(code.codeRepository), commit: str(code.version), image: str(obj(run.environment).value),
    client: str(agent.name), model: str(agent.version), seed: run.seed == null ? "" : str(run.seed),
    started: str(run.startTime), ended: str(run.endTime), description: str(root.description),
    inputs: items(run.object), outputs: items(run.result), parameters: run.parameters ?? {},
  };
}
