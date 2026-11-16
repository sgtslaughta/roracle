import { CircleCheck, CircleDashed, CircleX, Download, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import { REPRO_NOTE_MAX, type Evidence, type Note, type ReproOutcome, type ScanStatus } from "@/api/notes";
import type { Me } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { OverflowList } from "@/components/OverflowList";
import { Button } from "@/components/ui/button";
import { EvidenceDialog } from "./EvidenceForm";
import { canDownload, crateView, downloadEvidence, useDeleteEvidence, useEvidence, useReproduce } from "./evidenceApi";

const STANCE_ICON = { supports: CircleCheck, refutes: CircleX, inconclusive: CircleDashed } as const;
const SCAN_LABEL: Record<ScanStatus, string> = {
  pending: "Scanning for viruses…", clean: "Scanned clean", unscanned: "Not scanned",
  infected: "Blocked: infected", failed: "Blocked: scan failed",
};
const ITEMS_SHOWN = 3;
const EVIDENCE_SHOWN = 5;

export const fmtBytes = (n: number | null) =>
  n == null ? "" : n < 1024 ? `${n} B` : n < 1 << 20 ? `${(n / 1024).toFixed(1)} KB` : n < 1 << 30 ? `${(n / (1 << 20)).toFixed(1)} MB` : `${(n / (1 << 30)).toFixed(2)} GB`;
const fmtTime = (s: string) => { const d = new Date(s); return Number.isNaN(d.getTime()) ? s : d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" }); };

function Items({ label, list }: { label: string; list: { name: string; uri: string; sha256: string }[] }) {
  if (!list.length) return null;
  const text = (i: (typeof list)[number]) => [i.name, i.uri, i.sha256 && `sha256 ${i.sha256}`].filter(Boolean).join(" · ");
  return (
    <>
      <dt>{label}</dt>
      <dd><OverflowList items={list} max={ITEMS_SHOWN} menuLabel={`More ${label.toLowerCase()}`} getLabel={text} layout="inline"
        renderItem={(i, k) => <span key={k} className="badge-o" title={text(i)}><span className="t">{i.name}</span></span>} /></dd>
    </>
  );
}

function Reproduce({ ev, noteId, docId }: { ev: Evidence; noteId: string; docId: string }) {
  const repro = useReproduce(noteId, docId);
  const [note, setNote] = useState("");
  const mine = ev.reproductions.mine;
  const put = (o: ReproOutcome) => repro.mutate({ id: ev.id, outcome: mine === o ? null : o, note: mine === o ? "" : note }, {
    onError: () => toast("Your result couldn't be saved. Try again."),
  });
  return (
    <div className="row2 evrepro" role="group" aria-label="Your reproduction">
      <span>Did you rerun it?</span>
      <Button variant="ghost" aria-pressed={mine === "reproduced"} disabled={repro.isPending} onClick={() => put("reproduced")}>Reproduced</Button>
      <Button variant="ghost" aria-pressed={mine === "failed"} disabled={repro.isPending} onClick={() => put("failed")}>Failed</Button>
      <input aria-label="Reproduction note (optional)" placeholder="note (optional)" value={note} maxLength={REPRO_NOTE_MAX} onChange={(e) => setNote(e.target.value)} />
    </div>
  );
}

function EvidenceItemCard({ ev, noteId, docId, me }: { ev: Evidence; noteId: string; docId: string; me: Me | undefined }) {
  const c = crateView(ev.crate);
  const del = useDeleteEvidence(noteId, docId);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const Icon = STANCE_ICON[ev.stance];
  const r = ev.reproductions;
  const download = async () => {
    setBusy(true);
    try { await downloadEvidence(ev); } catch (e) {
      toast(e instanceof ApiError && e.status === 409 ? "This file isn't available for download yet." : "The download failed. Try again.");
    } finally { setBusy(false); }
  };
  return (
    <li className="ev" aria-label={`Evidence ${ev.stance}`}>
      <div className="row2">
        <span className={`tpill stance s-${ev.stance}`}><Icon aria-hidden />Evidence {ev.stance}</span>
        <span className="badge-o">Reproduced ×{r.reproduced}</span>
        <span className="badge-o">Failed ×{r.failed}</span>
        <span className="grow" />
        {ev.file && (canDownload(ev)
          ? <Button variant="ghost" disabled={busy} onClick={() => void download()}><Download aria-hidden />Download</Button>
          : <span className="flag">{SCAN_LABEL[ev.file.scan_status] ?? ev.file.scan_status}</span>)}
        {me && (me.id === ev.created_by || me.is_admin) && (
          <button className="icon-btn" aria-label="Delete evidence" onClick={() => setConfirm(true)}><Trash2 aria-hidden /></button>
        )}
      </div>
      <dl>
        <dt>Code</dt><dd>{c.repo} @ {c.commit}</dd>
        <dt>Environment</dt><dd>{c.image}</dd>
        <dt>Agent</dt><dd>{c.client} · {c.model}</dd>
        {c.seed && <><dt>Seed</dt><dd>{c.seed}</dd></>}
        <dt>Ran</dt><dd>{fmtTime(c.started)} – {fmtTime(c.ended)}</dd>
        <Items label="Inputs" list={c.inputs} />
        <Items label="Outputs" list={c.outputs} />
        {ev.file && <><dt>File</dt><dd>{[ev.file.filename || "evidence", fmtBytes(ev.file.size), ev.file.scan_status === "clean" || ev.file.scan_status === "unscanned" ? SCAN_LABEL[ev.file.scan_status] : ""].filter(Boolean).join(" · ")}<br /><span className="mono">sha256 {ev.file.sha256}</span></dd></>}
        {ev.uri && <><dt>Link</dt><dd>{ev.uri}<br /><span className="mono">sha256 {ev.uri_sha256}</span></dd></>}
        {c.description && <><dt>Description</dt><dd className="pre">{c.description}</dd></>}
      </dl>
      <details className="evparams">
        <summary>Parameters</summary>
        <pre>{JSON.stringify(c.parameters, null, 2)}</pre>
      </details>
      <Reproduce ev={ev} noteId={noteId} docId={docId} />
      <ConfirmDialog open={confirm} onOpenChange={setConfirm} title="Delete this evidence?" confirm="Delete" busy={del.isPending}
        onConfirm={() => del.mutate(ev.id, { onSuccess: () => setConfirm(false), onError: () => toast("The evidence couldn't be deleted. Try again.") })}>
        Its file, metadata and reproductions are removed for everyone.
      </ConfirmDialog>
    </li>
  );
}

/** Evidence on a finding note: listed when the note has any, plus "Add evidence" (web sign-in only; agents propose instead). */
export function EvidencePanel({ note, docId, me }: { note: Note; docId: string; me: Me | undefined }) {
  const total = note.evidence.supports + note.evidence.refutes + note.evidence.inconclusive;
  const { data, isError } = useEvidence(note.id, total > 0);
  const [adding, setAdding] = useState(false);
  const [limit, setLimit] = useState(EVIDENCE_SHOWN);
  return (
    <section className="evpanel" aria-label="Evidence">
      {isError && <p className="chipin-err" role="alert">Evidence couldn't be loaded.</p>}
      {!!data?.length && <ul className="evlist">{data.slice(0, limit).map((ev) => <EvidenceItemCard key={ev.id} ev={ev} noteId={note.id} docId={docId} me={me} />)}</ul>}
      {data && data.length > limit && <button className="btn-link" onClick={() => setLimit(limit + EVIDENCE_SHOWN)}>Show more evidence ({data.length - limit} left)</button>}
      <button className="btn-link" onClick={() => setAdding(true)}><Plus aria-hidden />Add evidence</button>
      {adding && <EvidenceDialog noteId={note.id} docId={docId} open={adding} onOpenChange={setAdding} />}
    </section>
  );
}
