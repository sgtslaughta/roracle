import { Loader2 } from "lucide-react";
import { useState } from "react";
import type { Citations, CiteItem, DraftDetail, Validation } from "@/api/drafts";
import { OverflowList } from "@/components/OverflowList";
import { Button } from "@/components/ui/button";
import { draftError, useAckCitation, useCheckCitations, useReport, useValidateDraft } from "./api";

const stale = (r: { version: number } | null | undefined, draft: DraftDetail) => !!r && r.version !== draft.latest_version;
const Stale = ({ v, latest }: { v: number; latest: number }) => <p className="sub" role="status">This result is for version {v}; the latest is {latest}. Run it again.</p>;

function Cite({ it, id }: { it: CiteItem; id: string }) {
  const ack = useAckCitation(id);
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const needs = it.status === "resolved" && it.verdict !== "supported";
  return (
    <div className="cite" data-row key={`${it.key}:${it.sentence_hash}`}>
      <div><b>{it.key}</b> <span className="badge-o">{it.status === "unresolved" ? "unresolved" : it.verdict ?? "resolved"}</span>{it.acknowledged && <span className="badge-o"> acknowledged</span>}</div>
      <p className="ptext">{it.sentence}</p>
      {it.reason && <small>{it.reason}</small>}
      {needs && !it.acknowledged && !open && <Button onClick={() => setOpen(true)}>Acknowledge…</Button>}
      {needs && it.acknowledged && <Button variant="ghost" onClick={() => ack.mutate({ key: it.key, sentence_hash: it.sentence_hash })}>Remove acknowledgement</Button>}
      {open && (
        <form className="dform inline" onSubmit={(e) => { e.preventDefault(); ack.mutate({ key: it.key, sentence_hash: it.sentence_hash, reason: reason.trim() }, { onSuccess: () => setOpen(false) }); }}>
          <input className="inp" aria-label={`Reason for ${it.key}`} maxLength={500} required placeholder="Why is this citation acceptable?" value={reason} onChange={(e) => setReason(e.target.value)} />
          <Button type="submit" variant="primary" disabled={!reason.trim() || ack.isPending}>Acknowledge</Button>
        </form>
      )}
      {ack.isError && <p role="alert" className="derr">{draftError(ack.error)}</p>}
    </div>
  );
}

/** Validate and citation check: POST results are kept in the query cache (the API has no GET) and go stale after each save. */
export function ChecksPanel({ draft, author }: { draft: DraftDetail; author: boolean }) {
  const validate = useValidateDraft(draft.id), check = useCheckCitations(draft.id);
  const v = useReport<Validation>(draft.id, "validation").data, c = useReport<Citations>(draft.id, "citations").data;
  if (!author) return <div className="dpanel"><p className="sub">Only the owner and co-authors can run checks.</p></div>;
  return (
    <div className="dpanel">
      <h3>Validation</h3>
      <Button onClick={() => validate.mutate()} disabled={validate.isPending}>{validate.isPending && <Loader2 className="spin" aria-hidden />}Run validation</Button>
      {validate.isError && <p role="alert" className="derr">{draftError(validate.error)}</p>}
      {v && (
        <div aria-label="Validation results">
          {stale(v, draft) && <Stale v={v.version} latest={draft.latest_version} />}
          <p><span className={`pill ${v.ok ? "done" : "failed"}`}>{v.ok ? "No blocking findings" : `${v.blocking.length} blocking`}</span> <small>profile {v.profile.name}</small></p>
          {v.style_unavailable && <p className="sub">Style checks were unavailable for this run.</p>}
          <OverflowList className="flist" items={[...v.blocking.map((f) => ({ f, b: true })), ...v.warnings.map((f) => ({ f, b: false }))]} max={8} menuLabel="More findings" moreLabel={(n) => `+${n} more findings`}
            renderItem={({ f, b }, i) => <p key={i} className="fitem">{b ? <b>Blocking </b> : "Warning "}{f.rule}: {f.message}{f.line != null && ` (line ${f.line})`}</p>} />
        </div>
      )}
      <h3>Citation check</h3>
      <Button onClick={() => check.mutate()} disabled={check.isPending}>{check.isPending && <Loader2 className="spin" aria-hidden />}Check citations</Button>
      {check.isError && <p role="alert" className="derr">{draftError(check.error)}</p>}
      {c && (
        <div aria-label="Citation results">
          {stale(c, draft) && <Stale v={c.version} latest={draft.latest_version} />}
          <p><span className={`pill ${c.ok ? "done" : "failed"}`}>{c.ok ? "All citations pass" : "Needs attention"}</span></p>
          <OverflowList className="vlist" items={c.items} max={8} menuLabel="More citations" getLabel={(i) => i.key} moreLabel={(n) => `+${n} more citations`}
            renderItem={(it) => <Cite key={`${it.key}:${it.sentence_hash}`} it={it} id={draft.id} />} />
        </div>
      )}
    </div>
  );
}
