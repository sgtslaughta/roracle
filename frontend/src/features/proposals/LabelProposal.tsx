import { ArrowRight, ArrowUpRight, Bot, Check, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import { SensitivityBadge } from "@/components/SensitivityBadge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { fmtDate } from "@/lib/dates";
import { useLabels } from "@/features/labels/api";
import { useMe } from "@/features/library/useMe";
import { useDoc } from "@/features/reader/api";
import { openDoc } from "@/features/reader/useReaderStore";
import { useApprove, useReject, type Proposal } from "./api";

const fail = (e: unknown) => (e instanceof ApiError && [403, 409, 422].includes(e.status) ? e.message : "That didn't work. Try again.");

/** An agent asks for a different sensitivity label on a document. Raising is for its owner; lowering needs an admin (and a reason when the label asks for one). */
export function LabelProposal({ p, selected, onSelect }: { p: Proposal; selected?: boolean; onSelect?: (on: boolean) => void }) {
  const approve = useApprove(), reject = useReject();
  const labels = useLabels().data ?? [];
  const doc = p.document;
  const current = useDoc(doc?.id ?? "").data?.label;
  const target = labels.find((l) => l.id === p.payload.label_id);
  const lowering = !!current && !!target && target.rank < current.rank;
  const admin = !!useMe().data?.is_admin; // only admins can lower, so only they are asked for a reason
  const needReason = lowering && admin && !!current?.gates.reason_on_lower;
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const busy = approve.isPending || reject.isPending;
  const title = doc?.title || "Untitled";
  return (
    <article className="prop" aria-label={`Label change proposed by ${p.client}`}>
      <div className="prop-main">
        <div className="nhead">
          {onSelect && <Checkbox checked={!!selected} onCheckedChange={(c) => onSelect(c === true)} aria-label={`Select label change on ${doc?.title || "missing document"}`} />}
          <span className="tpill t-comment">Label change</span>
          <span>on</span>
          {doc
            ? <button className="badge-o" title={title} onClick={() => openDoc(doc.id)}><span className="t">{title}</span><ArrowUpRight aria-hidden /></button>
            : <span className="badge-o">Document no longer available</span>}
          <span className="badge-o"><Bot aria-hidden />Agent-assisted</span>
          <span className="badge-o" title="Agent client">{p.client}</span>
        </div>
        <div className="nhead" aria-label="Proposed change">
          {current ? <SensitivityBadge label={current} /> : <span className="badge-o">Current label</span>}
          <ArrowRight aria-hidden />
          {target ? <SensitivityBadge label={target} /> : <span className="badge-o">A label that no longer exists</span>}
          {lowering && <span className="flag">Lowers the label: admins only</span>}
        </div>
        {p.status === "pending" && lowering && admin && (
          <label>Reason for lowering{needReason ? " (required)" : " (optional)"}
            <textarea className="ta short" maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
          </label>
        )}
        {error && <p className="chipin-err" role="alert">{error}</p>}
      </div>
      <div className="side2">
        {p.status === "pending" ? (
          <>
            <Button variant="primary" disabled={busy || !target || (needReason && reason.trim().length < 3)} onClick={() => {
              setError(null);
              approve.mutate({ id: p.id, body: lowering && reason.trim() ? { reason: reason.trim() } : {} }, {
                onSuccess: () => toast("Approved. The label was changed."), onError: (e) => setError(fail(e)),
              });
            }}><Check aria-hidden />Approve</Button>
            <Button variant="ghost" disabled={busy} onClick={() => reject.mutate(p.id, { onSuccess: () => toast("Proposal rejected."), onError: (e) => setError(fail(e)) })}><X aria-hidden />Reject</Button>
          </>
        ) : <span className="sub">{p.status === "approved" ? "Approved" : "Rejected"}{p.decided_at && ` ${fmtDate(p.decided_at)}`}</span>}
      </div>
    </article>
  );
}
