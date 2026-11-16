import { ArrowUpRight, Bot, Check, Pencil, X } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import { NOTE_BODY_MAX, NOTE_SHARES_MAX, PRINCIPAL_MAX, type NoteShare, type NoteVisibility } from "@/api/notes";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { invalidText } from "@/features/agents/api";
import { ChipInput } from "@/features/ask/ChipInput";
import { notesUi } from "@/features/notes/api";
import { KIND_LABEL, VIS_LABEL } from "@/features/notes/labels";
import { KindPill } from "@/features/notes/NoteCard";
import { useDoc } from "@/features/reader/api";
import { openDoc, setView } from "@/features/reader/useReaderStore";
import { useApprove, useReject, type ApproveBody, type Proposal } from "./api";

const tooLong = (v: string) => (v.length > PRINCIPAL_MAX ? `Keep each entry under ${PRINCIPAL_MAX} characters.` : null);
const groupError = (v: string) => tooLong(v) ?? (v.startsWith("/") ? null : "Group paths start with “/”.");
const errText = (e: unknown, what: string) =>
  e instanceof ApiError && (e.status === 422 || e.status === 409) ? invalidText(e) : `The proposal couldn't be ${what}. Try again.`;

/** Open the approved note in the reader's Notes tab and flash it. */
export function showNote(docId: string, noteId: string) {
  openDoc(docId);
  setView("notes");
  notesUi.set((s) => ({ ...s, flash: noteId }));
}

export const proposalLabel = (p: Proposal) => (p.payload.parent_id ? "Reply" : KIND_LABEL[p.payload.kind ?? "comment"]);

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
  const gRef = useRef<() => string[] | null>(null), uRef = useRef<() => string[] | null>(null);
  const total = groups.length + users.length;
  const full = () => (total >= NOTE_SHARES_MAX ? `At most ${NOTE_SHARES_MAX} people and groups in all.` : null);

  const submit = () => {
    setError(null);
    const b: ApproveBody = { body };
    if (!reply) {
      b.visibility = vis;
      if (vis === "shared") {
        const g = gRef.current ? gRef.current() : groups, u = uRef.current ? uRef.current() : users;
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
          <ChipInput label="People (user IDs)" values={users} onChange={setUsers} validate={(v) => tooLong(v) ?? full()} commitRef={uRef} />
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

interface Props { p: Proposal; selected?: boolean; onSelect?: (on: boolean) => void }

export function ProposalCard({ p, selected, onSelect }: Props) {
  const approve = useApprove(), reject = useReject();
  const [editing, setEditing] = useState(false);
  const pl = p.payload, doc = p.document;
  const label = proposalLabel(p);
  const busy = approve.isPending || reject.isPending;
  return (
    <article className="prop" aria-label={`${label} proposed by ${p.client}`}>
      <div className="prop-main">
        <div className="nhead">
          {onSelect && <Checkbox checked={!!selected} onCheckedChange={(c) => onSelect(c === true)} aria-label={`Select ${label} on ${doc?.title || "missing document"}`} />}
          {pl.parent_id ? <span className="tpill t-comment">Reply</span> : <KindPill kind={pl.kind ?? "comment"} />}
          <span>on</span>
          {doc
            ? <button className="badge-o" title={doc.title || "Untitled"} onClick={() => openDoc(doc.id, { page: pl.anchor?.page ?? undefined })}>
                <span className="t">{doc.title || "Untitled"}</span>{pl.anchor?.page != null && <>, page {pl.anchor.page}</>}<ArrowUpRight aria-hidden />
              </button>
            : <span className="badge-o">Document no longer available</span>}
          <span className="badge-o"><Bot aria-hidden />Agent-assisted</span>
          <span className="badge-o" title="Agent client">{p.client}</span>
          {!pl.parent_id && <span className="badge-o">{VIS_LABEL[pl.visibility ?? "private"]}</span>}
        </div>
        {pl.anchor?.quote && <blockquote className="nquote">{pl.anchor.quote}</blockquote>}
        {pl.relation && <div className="nhead nrel"><span>{pl.relation.type} →</span><Target id={pl.relation.target_document_id} /></div>}
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
        {p.status !== "pending" && <span className="sub">{p.status === "approved" ? "Approved" : "Rejected"}{p.decided_at && ` ${new Date(p.decided_at).toLocaleDateString()}`}</span>}
        {p.status === "approved" && p.annotation_id && doc && <Button onClick={() => showNote(doc.id, p.annotation_id!)}>Open note</Button>}
      </div>
    </article>
  );
}
