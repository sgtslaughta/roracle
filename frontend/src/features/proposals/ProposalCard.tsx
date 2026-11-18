import { fmtDate } from "@/lib/dates";
import { ArrowUpRight, Bot, Check, Pencil, X } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import { NOTE_BODY_MAX, NOTE_SHARES_MAX, PRINCIPAL_MAX, type NoteShare, type NoteVisibility } from "@/api/notes";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { invalidText } from "@/features/agents/api";
import { PeopleInput } from "@/components/PeopleInput";
import { ChipInput } from "@/features/ask/ChipInput";
import { showNote } from "@/features/notes/api";
import { KIND_LABEL, VIS_LABEL } from "@/features/notes/labels";
import { useDoc } from "@/features/reader/api";
import { openDoc } from "@/features/reader/useReaderStore";
import { isDocId } from "@/lib/ids";
import { clientName, useApprove, useReject, type ApproveBody, type Proposal } from "./api";
import { LabelProposal } from "./LabelProposal";

const tooLong = (v: string) => (v.length > PRINCIPAL_MAX ? `Keep each entry under ${PRINCIPAL_MAX} characters.` : null);
const groupError = (v: string) => tooLong(v) ?? (v.startsWith("/") ? null : "Group paths start with “/”.");
const errText = (e: unknown, what: string) =>
  e instanceof ApiError && (e.status === 422 || e.status === 409) ? invalidText(e) : `The proposal couldn't be ${what}. Try again.`;

/** What the agent wants, as the start of a sentence ending in the document title. */
export const proposalVerb = (p: Proposal) =>
  p.kind === "label" ? (p.payload.annotation_id ? "Change a note's label on" : "Change the label of")
    : p.payload.parent_id ? "Reply to a note on"
    : `Add a ${KIND_LABEL[p.payload.kind ?? "comment"].toLowerCase()} to`;

export const proposalLabel = (p: Proposal) => (p.kind === "label" ? "Label change" : p.payload.parent_id ? "Reply" : KIND_LABEL[p.payload.kind ?? "comment"]);

function Target({ id }: { id: string }) {
  const title = useDoc(id).data?.title || "a related document";
  return (
    <button className="badge-o" title={title} onClick={() => openDoc(id)}>
      <span className="t">{title}</span><ArrowUpRight aria-hidden />
    </button>
  );
}

function EditForm({ p, onDone }: { p: Proposal; onDone: () => void }) {
  const approve = useApprove();
  const reply = !!p.payload.parent_id;
  const sh = p.payload.shares ?? [];
  const [body, setBody] = useState(p.payload.body);
  const [vis, setVis] = useState<NoteVisibility>(p.payload.visibility ?? "private");
  const [groups, setGroups] = useState(sh.filter((s) => s.kind === "group").map((s) => s.principal));
  const [users, setUsers] = useState(sh.filter((s) => s.kind === "user").map((s) => s.principal));
  const [error, setError] = useState<string | null>(null);
  const gRef = useRef<() => string[] | null>(null);
  const total = groups.length + users.length;
  const full = () => (total >= NOTE_SHARES_MAX ? `At most ${NOTE_SHARES_MAX} people and groups in all.` : null);

  const submit = () => {
    setError(null);
    const b: ApproveBody = { body };
    if (!reply) {
      b.visibility = vis;
      if (vis === "shared") {
        const g = gRef.current ? gRef.current() : groups, u = users;
        if (!g || !u || !(g.length + u.length) || g.length + u.length > NOTE_SHARES_MAX) return;
        b.shares = [...g.map((x): NoteShare => ({ kind: "group", principal: x })), ...u.map((x): NoteShare => ({ kind: "user", principal: x }))];
      }
    }
    approve.mutate({ id: p.id, body: b }, {
      onSuccess: () => { toast("Approved with your edits. The note is now on the document."); onDone(); },
      onError: (e) => setError(errText(e, "approved")),
    });
  };

  return (
    <div className="agent-form" role="group" aria-label="Edit proposal">
      <label>{reply ? "Reply" : "Note"}
        <textarea className="ta" value={body} maxLength={NOTE_BODY_MAX} onChange={(e) => setBody(e.target.value)} />
      </label>
      {!reply && (
        <label>Visible to
          <select className="sel" value={vis} onChange={(e) => setVis(e.target.value as NoteVisibility)}>
            {(Object.keys(VIS_LABEL) as NoteVisibility[]).map((v) => <option key={v} value={v}>{VIS_LABEL[v]}</option>)}
          </select>
        </label>
      )}
      {!reply && vis === "shared" && (
        <>
          <ChipInput label="Groups" values={groups} onChange={setGroups} validate={(v) => groupError(v) ?? full()} commitRef={gRef} placeholder="/Mathematics/Spectral Theory" />
          <PeopleInput label="People" values={users} onChange={setUsers} full={full()} />
        </>
      )}
      {error && <p className="chipin-err" role="alert">{error}</p>}
      <div className="foot">
        <Button variant="ghost" onClick={onDone}>Cancel</Button>
        <Button variant="primary" disabled={!body.trim() || approve.isPending || (!reply && vis === "shared" && !total)} onClick={submit}>
          <Check aria-hidden />Approve with edits
        </Button>
      </div>
    </div>
  );
}

export interface Props { p: Proposal; selected?: boolean; onSelect?: (on: boolean) => void }

export const ProposalCard = (props: Props) => (props.p.kind === "label" ? <LabelProposal {...props} /> : <NoteProposal {...props} />);

function NoteProposal({ p, selected, onSelect }: Props) {
  const approve = useApprove(), reject = useReject();
  const [editing, setEditing] = useState(false);
  const pl = p.payload, doc = p.document;
  const label = proposalLabel(p);
  const busy = approve.isPending || reject.isPending;
  return (
    <article className="prop" aria-label={`${label} proposed by ${clientName(p.client)}`}>
      <div className="prop-main">
        <div className="psent">
          {onSelect && <Checkbox checked={!!selected} onCheckedChange={(c) => onSelect(c === true)} aria-label={`Select ${label} on ${doc?.title || "missing document"}`} />}
          <span><b>{proposalVerb(p)}</b>{" "}
            {doc
              ? <button className="plink" title={doc.title || "Untitled"} onClick={() => openDoc(doc.id, { page: pl.anchor?.page ?? undefined })}>
                  {doc.title || "Untitled"}{pl.anchor?.page != null && <>, page {pl.anchor.page}</>}<ArrowUpRight aria-hidden />
                </button>
              : <span className="sub">a document that is no longer available</span>}
          </span>
        </div>
        <p className="pmeta"><Bot aria-hidden />Agent-assisted, from {clientName(p.client)}{!pl.parent_id && <>. Visible to: {VIS_LABEL[pl.visibility ?? "private"]}</>}. Proposed {fmtDate(p.created_at)}.</p>
        {pl.anchor?.quote && <blockquote className="nquote">{pl.anchor.quote}</blockquote>}
        {pl.relation && isDocId(pl.relation.target_document_id) && <div className="nhead nrel"><span>{pl.relation.type} →</span><Target id={pl.relation.target_document_id} /></div>}
        {editing ? <EditForm p={p} onDone={() => setEditing(false)} /> : <div className="ntext ptext">{pl.body}</div>}
      </div>
      <div className="side2">
        {p.status === "pending" && !editing && (
          <>
            <Button variant="primary" disabled={busy} onClick={() => approve.mutate({ id: p.id, body: {} }, {
              onSuccess: () => toast("Approved. The note is now on the document."),
              onError: (e) => toast(errText(e, "approved")),
            })}><Check aria-hidden />Approve</Button>
            <Button disabled={busy} onClick={() => setEditing(true)}><Pencil aria-hidden />Edit</Button>
            <Button variant="ghost" disabled={busy} onClick={() => reject.mutate(p.id, {
              onSuccess: () => toast("Proposal rejected."),
              onError: (e) => toast(errText(e, "rejected")),
            })}><X aria-hidden />Reject</Button>
          </>
        )}
        {p.status !== "pending" && <span className="sub">{p.status === "approved" ? "Approved" : "Rejected"}{p.decided_at && ` ${fmtDate(p.decided_at)}`}</span>}
        {p.status === "approved" && p.annotation_id && doc && <Button onClick={() => showNote(doc.id, p.annotation_id!)}>Open note</Button>}
      </div>
    </article>
  );
}
