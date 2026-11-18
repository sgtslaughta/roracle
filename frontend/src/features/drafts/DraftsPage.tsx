import { useNavigate, Link } from "@tanstack/react-router";
import { PenLine, Plus } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { BUILTIN_PROFILES, TITLE_MAX, type DraftSummary } from "@/api/drafts";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { copyAction, RowMenu } from "@/components/RowMenu";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { fmtDate } from "@/lib/dates";
import { useMe } from "@/features/library/useMe";
import { draftError, useCreateDraft, useDeleteDraft, useDrafts } from "./api";
import { StatusBadge } from "./StatusBadge";
import { SensitivityBadge } from "@/components/SensitivityBadge";
import { RowSkeleton } from "@/components/RowSkeleton";

const PAGE = 12;

function NewDraft({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const nav = useNavigate();
  const create = useCreateDraft();
  const [title, setTitle] = useState("");
  const [profile, setProfile] = useState("");
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    create.mutate({ title: title.trim(), ...(profile ? { profile } : {}) }, { onSuccess: (d) => void nav({ to: "/drafts/$id", params: { id: d.id } }) });
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="w-[min(460px,calc(100vw-32px))]">
        <DialogTitle>New draft</DialogTitle>
        <DialogDescription>Pick a writing profile; its template and checks apply to this draft.</DialogDescription>
        <form className="dform" onSubmit={submit}>
          <label>Title
            <input className="inp" autoFocus required maxLength={TITLE_MAX} value={title} onChange={(e) => setTitle(e.target.value)} />
          </label>
          <label>Writing profile
            <select className="inp" value={profile} onChange={(e) => setProfile(e.target.value)}>
              <option value="">Default</option>
              {BUILTIN_PROFILES.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </label>
          {create.isError && <p role="alert" className="derr">{draftError(create.error)}</p>}
          <div className="foot">
            <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button variant="primary" type="submit" disabled={!title.trim() || create.isPending}>Create draft</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function DraftsPage() {
  const { data, isPending, isError } = useDrafts();
  const me = useMe().data;
  const del = useDeleteDraft();
  const [creating, setCreating] = useState(false);
  const [shown, setShown] = useState(PAGE);
  const [doomed, setDoomed] = useState<DraftSummary | null>(null);
  const items = data ?? [];

  const row = (d: DraftSummary) => (
    <div className="drow" data-row key={d.id}>
      <div className="dmain">
        <Link to="/drafts/$id" params={{ id: d.id }}><b>{d.title}</b></Link>
        <small>{d.profile ?? "default profile"} · v{d.latest_version} · {d.owner.id === me?.id ? "you" : d.owner.username} · {fmtDate(d.updated_at)}</small>
        <SensitivityBadge label={d.label} />
      </div>
      <StatusBadge status={d.status} />
      <RowMenu label={`Actions for ${d.title}`} actions={[
        copyAction("Copy link", `${location.origin}/drafts/${d.id}`),
        ...(d.owner.id === me?.id && d.status !== "submitted" && !d.published ? [{ label: "Delete draft", danger: true, onSelect: () => setDoomed(d) }] : []),
      ]} />
    </div>
  );

  return (
    <section className="view">
      <div className="uhead-row">
        <div>
          <h1>Drafts</h1>
          <p className="sub">Papers you're writing with an agent. Each draft is checked against its writing profile before you can submit it.</p>
        </div>
        <Button variant="primary" onClick={() => setCreating(true)}><Plus aria-hidden />New draft</Button>
      </div>
      {isPending && <RowSkeleton label="Loading drafts…" />}
      {isError && <div className="empty" role="alert"><p>Couldn't load your drafts.</p></div>}
      {data && !items.length && (
        <div className="empty"><PenLine /><p>No drafts yet. Start one here, or ask an agent to begin a draft for you.</p></div>
      )}
      {items.length > 0 && (
        <div className="dlist">
          {items.slice(0, shown).map(row)}
          {items.length > shown && <Button onClick={() => setShown((n) => n + PAGE)}>Show more ({items.length - shown} hidden)</Button>}
        </div>
      )}
      <NewDraft open={creating} onOpenChange={setCreating} />
      <ConfirmDialog open={!!doomed} onOpenChange={(o) => !o && setDoomed(null)} title="Delete this draft?" confirm="Delete" busy={del.isPending}
        onConfirm={() => doomed && del.mutate(doomed.id, { onSuccess: () => { setDoomed(null); toast("Draft deleted."); }, onError: (e) => { setDoomed(null); toast(draftError(e)); } })}>
        “{doomed?.title}” and its versions will be removed.
      </ConfirmDialog>
    </section>
  );
}
