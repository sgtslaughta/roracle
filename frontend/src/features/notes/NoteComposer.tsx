import { X } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import {
  NOTE_BODY_MAX, NOTE_KINDS, NOTE_SHARES_MAX, PRINCIPAL_MAX, QUOTE_MAX, RELATION_TYPES,
  type Note, type NoteIn, type NoteKind, type NotePatch, type NoteShare, type NoteVisibility, type RelationType,
} from "@/api/notes";
import { Button } from "@/components/ui/button";
import { invalidText } from "@/features/agents/api";
import { ChipInput } from "@/features/ask/ChipInput";
import { anchorBbox } from "./anchor";
import { notesUi, useCreateNote, usePatchNote, type NoteDraft } from "./api";
import { KIND_LABEL, VIS_LABEL } from "./labels";
import { TargetPicker, type Target } from "./TargetPicker";

const tooLong = (v: string) => (v.length > PRINCIPAL_MAX ? `Keep each entry under ${PRINCIPAL_MAX} characters.` : null);
const groupError = (v: string) => tooLong(v) ?? (v.startsWith("/") ? null : "Group paths start with “/”, for example /Mathematics/Spectral Theory.");
const errText = (e: unknown) => (e instanceof ApiError && e.status === 422 ? invalidText(e) : "The note couldn't be saved. Try again.");

interface Props {
  docId: string;
  /** edit an existing note (body, and visibility/shares for a top-level note); otherwise create */
  note?: Note;
  /** picked passage from "Add note" mode */
  draft?: NoteDraft | null;
  onDone: () => void;
}

export function NoteComposer({ docId, note, draft, onDone }: Props) {
  const create = useCreateNote(docId), patch = usePatchNote(docId);
  const editing = !!note;
  const [kind, setKind] = useState<NoteKind>(note?.kind ?? "comment");
  const [body, setBody] = useState(note?.body ?? "");
  const [vis, setVis] = useState<NoteVisibility>(note?.visibility ?? "everyone");
  const [groups, setGroups] = useState(note?.shares.filter((s) => s.kind === "group").map((s) => s.principal) ?? []);
  const [users, setUsers] = useState(note?.shares.filter((s) => s.kind === "user").map((s) => s.principal) ?? []);
  const [relType, setRelType] = useState<RelationType>("extends");
  const [target, setTarget] = useState<Target | null>(null);
  const [quote, setQuote] = useState(draft?.quote ?? "");
  const [page, setPage] = useState(String(draft?.page ?? notesUi.get().page));
  const [error, setError] = useState<string | null>(null);
  const gRef = useRef<() => string[] | null>(null), uRef = useRef<() => string[] | null>(null);
  const reply = !!note?.parent_id;
  const total = groups.length + users.length;
  const full = () => (total >= NOTE_SHARES_MAX ? `At most ${NOTE_SHARES_MAX} people and groups in all.` : null);
  const pageNo = Number(page);
  const pageOk = Number.isInteger(pageNo) && pageNo >= 1;
  const busy = create.isPending || patch.isPending;
  const blocked = !body.trim() || (kind === "relation" && !target && !editing) || (vis === "shared" && !reply && !total) || (!!quote.trim() && !pageOk);

  const shares = (): NoteShare[] | null => {
    if (vis !== "shared") return [];
    const g = gRef.current ? gRef.current() : groups, u = uRef.current ? uRef.current() : users;
    if (!g || !u || !(g.length + u.length) || g.length + u.length > NOTE_SHARES_MAX) return null;
    return [...g.map((p) => ({ kind: "group" as const, principal: p })), ...u.map((p) => ({ kind: "user" as const, principal: p }))];
  };

  const submit = () => {
    setError(null);
    const opts = { onSuccess: () => { toast(editing ? "Note updated." : "Note posted."); onDone(); }, onError: (e: unknown) => setError(errText(e)) };
    if (note) {
      const p: NotePatch = {};
      if (body !== note.body) p.body = body;
      if (!reply) { const s = shares(); if (!s) return; p.visibility = vis; if (vis === "shared") p.shares = s; }
      patch.mutate({ id: note.id, patch: p }, opts);
      return;
    }
    const s = shares();
    if (!s) return;
    const q = quote.trim().slice(0, QUOTE_MAX);
    // the picked box only describes the picked page
    const bbox = draft?.bbox && pageNo === draft.page ? anchorBbox(draft.bbox) : null;
    const b: NoteIn = { kind, body, visibility: vis, ...(vis === "shared" && { shares: s }) };
    if (q) b.anchor = { quote: q, prefix: "", suffix: "", page: pageNo, bbox };
    if (kind === "relation" && target) b.relation = { type: relType, target_document_id: target.id };
    create.mutate(b, opts);
  };

  return (
    <div className="ncard ncompose agent-form" role="group" aria-label={editing ? "Edit note" : "New note"}>
      {!editing && (
        <div className="agent-row">
          <label>Kind
            <select className="sel" value={kind} onChange={(e) => setKind(e.target.value as NoteKind)}>
              {NOTE_KINDS.map((k) => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}
            </select>
          </label>
          {kind === "relation" && (
            <label>Relation
              <select className="sel" value={relType} onChange={(e) => setRelType(e.target.value as RelationType)}>
                {RELATION_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </label>
          )}
        </div>
      )}
      {!editing && kind === "relation" && <TargetPicker docId={docId} value={target} onChange={setTarget} />}
      {!editing && (
        <div className="agent-row nquote-row">
          <label className="grow">Quote from the page (optional)
            <textarea className="ta short" value={quote} maxLength={QUOTE_MAX} onChange={(e) => setQuote(e.target.value)}
              placeholder="Paste the passage, or use Add note on the page" />
          </label>
          <label>Page
            <input type="number" min={1} value={page} onChange={(e) => setPage(e.target.value)} aria-invalid={!!quote.trim() && !pageOk} />
          </label>
          {quote && <button type="button" className="icon-btn" aria-label="Remove quote" onClick={() => { setQuote(""); notesUi.set((s) => ({ ...s, draft: null })); }}><X aria-hidden /></button>}
        </div>
      )}
      <label>{reply ? "Reply" : "Note"}
        <textarea className="ta" value={body} maxLength={NOTE_BODY_MAX} onChange={(e) => setBody(e.target.value)} placeholder="Write your note. Markdown links and lists work." />
      </label>
      {editing && note && body !== note.body && note.votes.up + note.votes.down > 0 && <p className="chipin-help">Changing the text resets its votes.</p>}
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
          <p className="chipin-help">Press Enter to add. Add at least one person or group.</p>
        </>
      )}
      {error && <p className="chipin-err" role="alert">{error}</p>}
      <div className="foot">
        <Button variant="ghost" onClick={onDone}>Cancel</Button>
        <Button variant="primary" onClick={submit} disabled={busy || blocked}>{editing ? "Save" : "Post note"}</Button>
      </div>
    </div>
  );
}
