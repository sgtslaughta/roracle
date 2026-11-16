import { ArrowUpRight, Bot, EyeOff, FileSearch, Globe, Lock, Reply, Users } from "lucide-react";
import { forwardRef, useState, type ReactNode } from "react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import { NOTE_BODY_MAX, type Note } from "@/api/notes";
import type { Me } from "@/api/types";
import { SafeMarkdown } from "@/components/SafeMarkdown";
import { Button } from "@/components/ui/button";
import { invalidText } from "@/features/agents/api";
import { useDoc } from "@/features/reader/api";
import { openDoc } from "@/features/reader/useReaderStore";
import { cn } from "@/lib/utils";
import { useReply } from "./api";
import { KIND_ICON, KIND_LABEL, VIS_LABEL } from "./labels";
import { NoteMenu, NoteVote } from "./NoteActions";
import { NoteComposer } from "./NoteComposer";

export const REPLIES_SHOWN = 3;
const VIS_ICON = { everyone: Globe, shared: Users, private: Lock } as const;

interface Ctx { docId: string; me: Me | undefined; docOwnerId: string }
interface Props extends Ctx {
  note: Note;
  replies: Note[];
  viewedVersion: number;
  flash?: boolean;
  /** start with every reply shown (a flashed reply must be visible) */
  expand?: boolean;
  onGoTo: (page: number) => void;
  /** evidence panel (Task 3) renders here */
  evidence?: ReactNode;
}

export function KindPill({ kind }: { kind: Note["kind"] }) {
  const Icon = KIND_ICON[kind];
  return <span className={`tpill t-${kind}`}><Icon aria-hidden />{KIND_LABEL[kind]}</span>;
}

function RelationLine({ note }: { note: Note }) {
  const rel = note.relation!;
  const t = useDoc(rel.target_document_id).data;
  const title = t?.title || "a related document";
  return (
    <div className="nhead nrel">
      <span>{rel.type} →</span>
      <button className="badge-o" title={title} onClick={() => openDoc(rel.target_document_id)}>
        <span className="t">{title}</span><ArrowUpRight aria-hidden />
      </button>
    </div>
  );
}

function EvidencePill({ ev }: { ev: Note["evidence"] }) {
  const parts = ([["supports", ev.supports], ["refutes", ev.refutes], ["inconclusive", ev.inconclusive]] as const).filter(([, n]) => n > 0);
  if (!parts.length) return null;
  return <span className="badge-o"><FileSearch aria-hidden />Evidence: {parts.map(([k, n]) => `${n} ${k}`).join(" · ")}</span>;
}

/** Author, body and actions shared by notes and replies; editing swaps the body for the composer. */
function NoteBody({ note, docId, me, docOwnerId, children }: Ctx & { note: Note; children?: ReactNode }) {
  const [editing, setEditing] = useState(false);
  if (editing) return <NoteComposer docId={docId} note={note} onDone={() => setEditing(false)} />;
  return (
    <>
      <SafeMarkdown text={note.body} className="ntext" />
      <div className="nhead nfoot">
        <NoteVote note={note} docId={docId} own={!!me && me.id === note.author.id} />
        {children}
        <span className="grow" />
        <NoteMenu note={note} docId={docId} me={me} docOwnerId={docOwnerId} onEdit={() => setEditing(true)} />
      </div>
    </>
  );
}

function ReplyBox({ note, docId, onDone }: { note: Note; docId: string; onDone: () => void }) {
  const reply = useReply(docId);
  const [body, setBody] = useState("");
  return (
    <div className="nreply-box">
      <textarea className="ta short" aria-label={`Reply to ${note.author.username}`} value={body} maxLength={NOTE_BODY_MAX} onChange={(e) => setBody(e.target.value)} autoFocus />
      <div className="foot">
        <Button variant="ghost" onClick={onDone}>Cancel</Button>
        <Button variant="primary" disabled={!body.trim() || reply.isPending} onClick={() => reply.mutate({ id: note.id, body }, {
          onSuccess: () => { setBody(""); onDone(); },
          onError: (e) => toast(e instanceof ApiError && e.status === 422 ? invalidText(e) : "The reply couldn't be posted. Try again."),
        })}>Post reply</Button>
      </div>
    </div>
  );
}

export const NoteCard = forwardRef<HTMLDivElement, Props>(function NoteCard({ note, replies, viewedVersion, flash, expand, onGoTo, evidence, ...ctx }, ref) {
  const [more, setMore] = useState(false);
  const all = more || !!expand;
  const [replying, setReplying] = useState(false);
  const VisIcon = VIS_ICON[note.visibility];
  const a = note.anchor;
  const shown = all ? replies : replies.slice(0, REPLIES_SHOWN);
  return (
    <article ref={ref} id={`note-${note.id}`} className={cn("ncard", flash && "flash")} aria-label={`${KIND_LABEL[note.kind]} by ${note.author.username}`}>
      <div className="nhead">
        <KindPill kind={note.kind} />
        <b>{note.author.username}</b>
        {note.agent_assisted && <span className="badge-o"><Bot aria-hidden />Agent-assisted</span>}
        <span className="badge-o"><VisIcon aria-hidden />{VIS_LABEL[note.visibility]}</span>
        {note.status === "hidden" && <span className="badge-o"><EyeOff aria-hidden />Hidden</span>}
        {a?.page != null && <button className="badge-o" onClick={() => onGoTo(a.page!)}>Page {a.page}</button>}
        {note.may_not_apply_to_version != null
          ? <span className="flag">May not apply to version {note.may_not_apply_to_version}</span>
          : note.version != null && note.version !== viewedVersion && <span className="badge-o">On version {note.version}</span>}
      </div>
      {a && <blockquote className="nquote">{a.quote}</blockquote>}
      {note.relation && <RelationLine note={note} />}
      <NoteBody note={note} {...ctx}>
        <EvidencePill ev={note.evidence} />
        <button className="btn-link" onClick={() => setReplying(true)}><Reply aria-hidden />Reply</button>
      </NoteBody>
      {evidence}
      {(replies.length > 0 || replying) && (
        <ol className="nreplies" aria-label={`${replies.length} ${replies.length === 1 ? "reply" : "replies"}`}>
          {shown.map((r) => (
            <li key={r.id} id={`note-${r.id}`} className="nreply">
              <div className="nhead"><b>{r.author.username}</b>{r.agent_assisted && <span className="badge-o"><Bot aria-hidden />Agent-assisted</span>}
                {r.status === "hidden" && <span className="badge-o"><EyeOff aria-hidden />Hidden</span>}</div>
              <NoteBody note={r} {...ctx} />
            </li>
          ))}
          {!all && replies.length > REPLIES_SHOWN && (
            <li><button className="btn-link" onClick={() => setMore(true)}>Show {replies.length - REPLIES_SHOWN} more {replies.length - REPLIES_SHOWN === 1 ? "reply" : "replies"}</button></li>
          )}
          {replying && <li><ReplyBox note={note} docId={ctx.docId} onDone={() => setReplying(false)} /></li>}
        </ol>
      )}
    </article>
  );
});
