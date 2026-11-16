import { Plus, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import {
  EVIDENCE_DESC_MAX, EVIDENCE_ITEMS_MAX, EVIDENCE_MAX_BYTES, EVIDENCE_PARAMS_MAX, STANCES,
  type EvidenceItem, type EvidenceMeta, type Stance,
} from "@/api/notes";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { invalidText } from "@/features/agents/api";
import { uploadEvidence, UploadAborted, useEvidenceAdded, type Upload } from "./evidenceApi";

/* client-side mirrors of the backend Provenance validators (the server stays the authority) */
const HEX64 = /^[0-9a-f]{64}$/;
const COMMIT = /^[0-9a-fA-F]{7,64}$/;
const DIGEST = /^sha256:[0-9a-f]{64}$/;
const URI = /^(https?|s3|doi|git|ssh):/i;
const GIT_SCP = /^[\w.-]+@[\w.-]+:\S+$/;

export interface Row { name: string; uri: string; sha256: string }
export interface Fields {
  stance: Stance; repo: string; commit: string; image: string; client: string; model: string;
  started: string; ended: string; seed: string; parameters: string; description: string;
  inputs: Row[]; outputs: Row[]; source: "file" | "uri" | "none"; uri: string; uriSha: string;
}
export const EMPTY_FIELDS: Fields = {
  stance: "supports", repo: "", commit: "", image: "", client: "", model: "", started: "", ended: "", seed: "",
  parameters: "{}", description: "", inputs: [], outputs: [], source: "file", uri: "", uriSha: "",
};

const hasAtKey = (v: unknown): boolean =>
  Array.isArray(v) ? v.some(hasAtKey) : !!v && typeof v === "object" && Object.entries(v).some(([k, x]) => k.startsWith("@") || hasAtKey(x));

function rows(list: Row[], label: string): EvidenceItem[] | string {
  const out: EvidenceItem[] = [];
  for (const [i, r] of list.entries()) {
    const name = r.name.trim(), uri = r.uri.trim(), sha = r.sha256.trim().toLowerCase();
    if (!name && !uri && !sha) continue; // blank row
    if (!name || name.length > 500) return `${label} ${i + 1}: give a name (up to 500 characters).`;
    if (uri && (!URI.test(uri) || uri.length > 2000)) return `${label} ${i + 1}: the link must start with http(s), s3, doi, git or ssh.`;
    if (sha && !HEX64.test(sha)) return `${label} ${i + 1}: SHA-256 must be 64 hex characters.`;
    out.push({ name, ...(uri ? { uri } : {}), ...(sha ? { sha256: sha } : {}) });
  }
  return out;
}

/** datetime-local (local wall time) -> ISO 8601 in UTC (an aware datetime). */
const iso = (v: string) => (v && !Number.isNaN(new Date(v).getTime()) ? new Date(v).toISOString() : "");

/** Form fields -> the multipart `meta` object, or the first problem as a sentence. */
export function buildMeta(f: Fields): { meta: EvidenceMeta } | { error: string } {
  const repo = f.repo.trim(), commit = f.commit.trim(), image = f.image.trim(), client = f.client.trim(), model = f.model.trim();
  if (!repo || repo.length > 2000 || !(GIT_SCP.test(repo) || URI.test(repo))) return { error: "Repository: a git URL (https, git, ssh) or user@host:path." };
  if (!COMMIT.test(commit)) return { error: "Commit: 7 to 64 hex characters." };
  if (!DIGEST.test(image)) return { error: "Image digest: sha256: followed by 64 lowercase hex characters." };
  if (!client || client.length > 200 || !model || model.length > 200) return { error: "Agent client and model are required (up to 200 characters each)." };
  const started_at = iso(f.started), ended_at = iso(f.ended);
  if (!started_at || !ended_at) return { error: "Give both the start and end time of the run." };
  if (started_at > ended_at) return { error: "The run can't end before it starts." };
  let parameters: unknown;
  try { parameters = JSON.parse(f.parameters.trim() || "{}"); } catch { return { error: "Parameters must be valid JSON." }; }
  if (!parameters || typeof parameters !== "object" || Array.isArray(parameters)) return { error: "Parameters must be a JSON object, e.g. {\"k\": 3}." };
  if (JSON.stringify(parameters).length > EVIDENCE_PARAMS_MAX) return { error: "Parameters must be under 64 KB." };
  if (hasAtKey(parameters)) return { error: "Parameter names can't start with @." };
  const inputs = rows(f.inputs, "Input"), outputs = rows(f.outputs, "Output");
  if (typeof inputs === "string") return { error: inputs };
  if (typeof outputs === "string") return { error: outputs };
  if (inputs.length > EVIDENCE_ITEMS_MAX || outputs.length > EVIDENCE_ITEMS_MAX) return { error: `Up to ${EVIDENCE_ITEMS_MAX} inputs and outputs each.` };
  if (f.description.length > EVIDENCE_DESC_MAX) return { error: `Description: up to ${EVIDENCE_DESC_MAX} characters.` };
  let uri: string | null = null, uri_sha256: string | null = null;
  if (f.source === "uri") {
    uri = f.uri.trim(); uri_sha256 = f.uriSha.trim().toLowerCase();
    if (!URI.test(uri) || uri.length > 2000) return { error: "Link: must start with http(s), s3, doi, git or ssh." };
    if (!HEX64.test(uri_sha256)) return { error: "Link SHA-256: 64 hex characters." };
  }
  const s = f.seed.trim();
  const seed = !s ? null : /^-?\d+$/.test(s) && Number.isSafeInteger(Number(s)) ? Number(s) : s;
  return { meta: {
    stance: f.stance, code: { repo, commit }, environment: { image_digest: image }, inputs, outputs,
    parameters: parameters as Record<string, unknown>, seed, agent: { client, model }, started_at, ended_at,
    description: f.description, uri, uri_sha256,
  } };
}

export const fileTooBig = (f: File | null) => !!f && f.size > EVIDENCE_MAX_BYTES;

function Rows({ label, list, onChange }: { label: string; list: Row[]; onChange: (r: Row[]) => void }) {
  const set = (i: number, patch: Partial<Row>) => onChange(list.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  return (
    <fieldset className="evrows">
      <legend>{label}s ({list.length}/{EVIDENCE_ITEMS_MAX})</legend>
      {list.map((r, i) => (
        <div key={i} className="evrow">
          <input aria-label={`${label} ${i + 1} name`} placeholder="name" value={r.name} maxLength={500} onChange={(e) => set(i, { name: e.target.value })} />
          <input aria-label={`${label} ${i + 1} link`} placeholder="link (optional)" value={r.uri} maxLength={2000} onChange={(e) => set(i, { uri: e.target.value })} />
          <input aria-label={`${label} ${i + 1} SHA-256`} placeholder="sha256 (optional)" value={r.sha256} maxLength={64} onChange={(e) => set(i, { sha256: e.target.value })} />
          <button type="button" className="icon-btn" aria-label={`Remove ${label.toLowerCase()} ${i + 1}`} onClick={() => onChange(list.filter((_, j) => j !== i))}><X aria-hidden /></button>
        </div>
      ))}
      <Button type="button" variant="ghost" disabled={list.length >= EVIDENCE_ITEMS_MAX} onClick={() => onChange([...list, { name: "", uri: "", sha256: "" }])}>
        <Plus aria-hidden />Add {label.toLowerCase()}
      </Button>
    </fieldset>
  );
}

export function EvidenceDialog({ noteId, docId, open, onOpenChange }: { noteId: string; docId: string; open: boolean; onOpenChange: (o: boolean) => void }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="w-[min(640px,calc(100vw-32px))] max-h-[calc(100vh-48px)] overflow-auto" onInteractOutside={(e) => e.preventDefault()}>
        <EvidenceForm noteId={noteId} docId={docId} onDone={() => onOpenChange(false)} />
      </DialogContent>
    </Dialog>
  );
}

export function EvidenceForm({ noteId, docId, onDone }: { noteId: string; docId: string; onDone: () => void }) {
  const [f, setF] = useState<Fields>(EMPTY_FIELDS);
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState("");
  const [progress, setProgress] = useState<{ sent: number; total: number } | null>(null);
  const upload = useRef<Upload | null>(null);
  const added = useEvidenceAdded(noteId, docId);
  useEffect(() => () => upload.current?.abort(), []); // closing the dialog cancels a running upload
  const set = <K extends keyof Fields>(k: K) => (v: Fields[K]) => setF((s) => ({ ...s, [k]: v }));
  const input = (k: "repo" | "commit" | "image" | "client" | "model" | "seed", label: string, ph = "", max = 200) => (
    <label>{label}<input value={f[k]} placeholder={ph} maxLength={max} onChange={(e) => set(k)(e.target.value)} /></label>
  );

  const submit = async () => {
    const r = buildMeta(f);
    if ("error" in r) return setError(r.error);
    if (f.source === "file" && !file) return setError("Choose a file, or switch to a link or metadata only.");
    if (f.source === "file" && fileTooBig(file)) return setError("Evidence files can be up to 1 GiB.");
    setError("");
    setProgress({ sent: 0, total: file?.size ?? 0 });
    const u = (upload.current = uploadEvidence(noteId, r.meta, f.source === "file" ? file : null, (sent, total) => setProgress({ sent, total })));
    try {
      await u.done;
      void added();
      toast("Evidence added");
      onDone();
    } catch (e) {
      if (e instanceof UploadAborted) return;
      const msg = !(e instanceof ApiError) ? "The evidence couldn't be added. Try again."
        : e.status === 413 ? "The file is larger than the server allows (1 GiB)."
        : e.status === 422 || e.status === 400 ? invalidText(e)
        : e.status === 403 ? "Evidence can only be added from the web app, signed in as yourself."
        : "The evidence couldn't be added. Try again.";
      setError(msg);
    } finally {
      if (upload.current === u) { upload.current = null; setProgress(null); }
    }
  };
  const busy = !!progress;
  const pct = progress?.total ? Math.round((progress.sent / progress.total) * 100) : 0;

  return (
    <form className="agent-form evform" onSubmit={(e) => { e.preventDefault(); if (!busy) void submit(); }}>
      <DialogTitle>Add evidence</DialogTitle>
      <DialogDescription>Record how the result was produced so others can rerun it. Files are virus-scanned before anyone can download them.</DialogDescription>
      <fieldset disabled={busy} className="evfields">
        <label>Stance
          <select className="sel" value={f.stance} onChange={(e) => set("stance")(e.target.value as Stance)}>
            {STANCES.map((s) => <option key={s} value={s}>{s[0].toUpperCase() + s.slice(1)}</option>)}
          </select>
        </label>
        <div className="evgrid">
          {input("repo", "Repository", "https://git.lab/team/repo", 2000)}
          {input("commit", "Commit", "4e1c9a2", 64)}
          {input("image", "Image digest", "sha256:…", 71)}
          {input("client", "Agent client", "claude-code")}
          {input("model", "Model", "model name")}
          <label>Started<input type="datetime-local" step={1} value={f.started} onChange={(e) => set("started")(e.target.value)} /></label>
          <label>Ended<input type="datetime-local" step={1} value={f.ended} onChange={(e) => set("ended")(e.target.value)} /></label>
        </div>
        <fieldset>
          <legend>Attach</legend>
          <div className="row2">
            {(["file", "uri", "none"] as const).map((s) => (
              <label key={s} className="ck"><input type="radio" name="evsource" checked={f.source === s} onChange={() => set("source")(s)} />{{ file: "A file", uri: "A link", none: "Metadata only" }[s]}</label>
            ))}
          </div>
        </fieldset>
        {f.source === "file" && (
          <label>File (up to 1 GiB)<input type="file" onChange={(e) => {
            const x = e.target.files?.[0] ?? null;
            setFile(x);
            setError(fileTooBig(x) ? "Evidence files can be up to 1 GiB." : "");
          }} /></label>
        )}
        {f.source === "uri" && (
          <div className="evgrid">
            <label>Link<input value={f.uri} maxLength={2000} placeholder="s3://bucket/run.tar" onChange={(e) => set("uri")(e.target.value)} /></label>
            <label>Link SHA-256<input value={f.uriSha} maxLength={64} onChange={(e) => set("uriSha")(e.target.value)} /></label>
          </div>
        )}
        <details className="evmore">
          <summary>More details: seed, parameters, inputs, outputs, description</summary>
          {input("seed", "Seed (optional)", "1729")}
          <label>Parameters (JSON object)<textarea className="ta short mono" spellCheck={false} value={f.parameters} onChange={(e) => set("parameters")(e.target.value)} /></label>
          <Rows label="Input" list={f.inputs} onChange={set("inputs")} />
          <Rows label="Output" list={f.outputs} onChange={set("outputs")} />
          <label>Description<textarea className="ta short" value={f.description} maxLength={EVIDENCE_DESC_MAX} onChange={(e) => set("description")(e.target.value)} /></label>
        </details>
      </fieldset>
      {error && <p className="chipin-err" role="alert">{error}</p>}
      {progress && (
        <div className="evprog">
          <div role="progressbar" aria-label="Upload progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}><span style={{ width: `${pct}%` }} /></div>
          <span>{pct}%</span>
        </div>
      )}
      <div className="foot">
        {busy
          ? <Button type="button" variant="ghost" onClick={() => upload.current?.abort()}>Cancel upload</Button>
          : <Button type="button" variant="ghost" onClick={onDone}>Cancel</Button>}
        <Button type="submit" variant="primary" disabled={busy}>Add evidence</Button>
      </div>
    </form>
  );
}
