import { Ellipsis, ThumbsDown, ThumbsUp } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import { REPORT_REASONS, type Note, type ReportReason } from "@/api/notes";
import type { Me } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { invalidText } from "@/features/agents/api";
import { cn } from "@/lib/utils";
import { reportNote, useDeleteNote, useHideNote, useRemoveNote, useVoteNote } from "./api";

const REASON_LABEL: Record<ReportReason, string> = { spam: "Spam", abusive: "Abusive", wrong: "Wrong or misleading", other: "Something else" };
const fail = (what: string) => (e: unknown) => toast(e instanceof ApiError && e.status === 422 ? invalidText(e) : `${what} didn't work. Try again.`);

/** Thumbs vote; disabled on your own note (the API answers 422). */
export function NoteVote({ note, docId, own }: { note: Note; docId: string; own: boolean }) {
  const vote = useVoteNote(docId);
  const { up, down, mine } = note.votes;
  const send = (v: 1 | -1) => vote.mutate({ id: note.id, value: mine === v ? 0 : v }, { onError: fail("Voting") });
  const title = own ? "You can't vote on your own note" : undefined;
  return (
    <div className="vote row nvote">
      <button className="up" aria-pressed={mine > 0} aria-label="Helpful" title={title ?? "Helpful"} disabled={own || vote.isPending} onClick={() => send(1)}>
        <ThumbsUp aria-hidden fill={mine > 0 ? "currentColor" : "none"} />
      </button>
      <span className={cn("score", up - down > 0 && "pos", up - down < 0 && "neg")} title={`${up} helpful, ${down} not helpful`}>{up - down}</span>
      <button className="down" aria-pressed={mine < 0} aria-label="Not helpful" title={title ?? "Not helpful"} disabled={own || vote.isPending} onClick={() => send(-1)}>
        <ThumbsDown aria-hidden fill={mine < 0 ? "currentColor" : "none"} />
      </button>
    </div>
  );
}

interface MenuProps { note: Note; docId: string; me: Me | undefined; docOwnerId: string; onEdit: () => void }

/** ⋯ menu. Shown items follow the contract: report (not your own), hide/unhide (document owner), edit/delete (author), remove (admin). */
export function NoteMenu({ note, docId, me, docOwnerId, onEdit }: MenuProps) {
  const [open, setOpen] = useState(false);
  const [reasons, setReasons] = useState(false);
  const [confirm, setConfirm] = useState<"delete" | "remove" | null>(null);
  const hide = useHideNote(docId), del = useDeleteNote(docId), remove = useRemoveNote(docId);
  const own = !!me && me.id === note.author.id;
  const owner = !!me && me.id === docOwnerId;
  const hidden = note.status === "hidden";
  const close = () => { setOpen(false); setReasons(false); };
  const item = (label: string, run: () => void) => <button key={label} role="menuitem" onClick={() => { close(); run(); }}>{label}</button>;

  const report = (r: ReportReason) => reportNote(note.id, r).then(() => toast("Reported. Moderators will take a look."), fail("Reporting"));

  return (
    <>
      <Popover open={open} onOpenChange={(o) => { setOpen(o); if (!o) setReasons(false); }}>
        <PopoverTrigger asChild>
          <button className="icon-btn" aria-label={`More actions for note by ${note.author.username}`}><Ellipsis aria-hidden /></button>
        </PopoverTrigger>
        <PopoverContent className="chipmenu" role="menu" aria-label="Note actions" align="end">
          {reasons ? (
            <>
              <p className="menu-cap">Why are you reporting it?</p>
              {REPORT_REASONS.map((r) => item(REASON_LABEL[r], () => void report(r)))}
            </>
          ) : (
            <>
              {!own && <button role="menuitem" onClick={() => setReasons(true)}>Report…</button>}
              {owner && item(hidden ? "Unhide" : "Hide from readers", () => hide.mutate({ id: note.id, hidden: !hidden }, {
                onSuccess: () => toast(hidden ? "Note visible again." : "Note hidden. Its author and admins still see it."), onError: fail("Hiding"),
              }))}
              {own && item("Edit", onEdit)}
              {own && item("Delete", () => setConfirm("delete"))}
              {me?.is_admin && item("Remove (admin)", () => setConfirm("remove"))}
            </>
          )}
        </PopoverContent>
      </Popover>
      <ConfirmDialog
        open={confirm !== null} onOpenChange={(o) => !o && setConfirm(null)}
        title={confirm === "remove" ? "Remove this note?" : "Delete this note?"}
        confirm={confirm === "remove" ? "Remove" : "Delete"} busy={del.isPending || remove.isPending}
        onConfirm={() => {
          const m = confirm === "remove" ? remove : del;
          m.mutate(note.id, { onSuccess: () => { setConfirm(null); toast(confirm === "remove" ? "Note removed." : "Note deleted."); }, onError: fail("Deleting") });
        }}
      >
        {confirm === "remove" ? "It disappears for everyone, including its author. The removal is audited." : "This can't be undone. Replies to it are deleted too."}
      </ConfirmDialog>
    </>
  );
}
