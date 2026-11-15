import { Bot, Copy, Pencil, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import { handoffLink, type HandoffManifest, type HandoffRow, type PatchHandoffBody } from "@/api/agents";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { useHandoff, useHandoffs, usePatchHandoff, useRevokeHandoff } from "./api";
import { copyText } from "./HandoffDialog";
import { AUDIENCE_LABEL, HandoffForm, type HandoffValues } from "./HandoffForm";
import { fmtDate } from "./TokensPanel";

const PAGE = 20;
const KIND_LABEL = { chat: "Chat", collection: "Collection", search: "Search", document: "Document", agent: "Agent" } as const;
const stateOf = (h: HandoffRow) => (h.revoked_at ? "Revoked" : new Date(h.expires_at) <= new Date() ? "Expired" : "Active");
const nameOf = (h: { title: string; display: string }) => h.title || `Handoff ${h.display}`;

export function HandoffsPanel() {
  const { data, isError } = useHandoffs();
  const revoke = useRevokeHandoff();
  const [target, setTarget] = useState<HandoffRow | null>(null);
  const [editing, setEditing] = useState<HandoffRow | null>(null);
  const [shown, setShown] = useState(PAGE);

  const doRevoke = () => {
    if (!target) return;
    revoke.mutate(target.code, {
      onSuccess: () => toast("Handoff revoked. Its link no longer loads."),
      onError: () => toast("The handoff couldn't be revoked. Try again."),
      onSettled: () => setTarget(null),
    });
  };

  return (
    <div className="agent-panel">
      <p className="sub">Handoffs you've created. Anyone in the audience can load one until it expires or you revoke it.</p>
      {isError && <p role="alert">Handoffs couldn't be loaded. Reload to try again.</p>}
      {data && !data.length && <div className="empty"><Bot aria-hidden /><p>No handoffs yet. Use “Continue in agent” on a chat, document or search.</p></div>}
      {data && data.length > 0 && (
        <ul className="hrows" aria-label="My handoffs">
          {data.slice(0, shown).map((h) => {
            const st = stateOf(h);
            return (
              <li key={h.code} className={st !== "Active" ? "dim" : undefined}>
                <div className="hrow-main">
                  <b className="trunc" title={nameOf(h)}>{nameOf(h)}</b>
                  <small><code>{h.display}</code> · {KIND_LABEL[h.kind]} · {AUDIENCE_LABEL[h.audience]} · <span>{st === "Active" ? `Expires ${fmtDate(h.expires_at)}` : st}</span>{h.created_via === "mcp" ? " · Made by an agent" : ""}</small>
                </div>
                {st === "Active" && (
                  <div className="row2">
                    <button className="icon-btn" aria-label={`Copy link to ${nameOf(h)}`} onClick={() => void copyText(handoffLink(`/h/${h.display}`), "Handoff link copied")}><Copy aria-hidden /></button>
                    <button className="icon-btn" aria-label={`Edit ${nameOf(h)}`} onClick={() => setEditing(h)}><Pencil aria-hidden /></button>
                    <button className="icon-btn" aria-label={`Revoke ${nameOf(h)}`} onClick={() => setTarget(h)}><X aria-hidden /></button>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {data && data.length > shown && <Button variant="ghost" onClick={() => setShown(shown + PAGE)}>Show {Math.min(PAGE, data.length - shown)} more</Button>}
      {editing && <EditHandoff row={editing} onClose={() => setEditing(null)} />}
      <ConfirmDialog open={!!target} onOpenChange={(o) => !o && setTarget(null)} title="Revoke this handoff?" confirm="Revoke" onConfirm={doRevoke} busy={revoke.isPending}>
        “{target && nameOf(target)}” stops loading for everyone, agents included. This can't be undone.
      </ConfirmDialog>
    </div>
  );
}

function EditHandoff({ row, onClose }: { row: HandoffRow; onClose: () => void }) {
  const { data: m, error } = useHandoff(row.code);
  const patch = usePatchHandoff();
  const save = (m: HandoffManifest, v: HandoffValues) => {
    const p: PatchHandoffBody = {};
    if (v.title !== m.title) p.title = v.title;
    if (v.goal !== m.goal) p.goal = v.goal;
    if (v.brief !== m.brief) p.brief = v.brief;
    if (v.audience !== m.audience) p.audience = v.audience;
    if (v.shares) p.shares = v.shares;
    if (v.days) p.expires_in_days = v.days;
    if (!Object.keys(p).length) return onClose();
    patch.mutate({ code: m.code, patch: p }, {
      onSuccess: () => { toast("Handoff updated"); onClose(); },
      onError: (e) => toast(e instanceof ApiError && e.status === 422 ? e.message : "The handoff couldn't be updated. Try again."),
    });
  };
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="w-[min(680px,calc(100vw-32px))]">
        <DialogTitle>Edit handoff {row.display}</DialogTitle>
        <DialogDescription>Changes apply the next time an agent or person loads it.</DialogDescription>
        {m ? <HandoffForm mode="edit" initial={m} busy={patch.isPending} submitLabel="Save" onSubmit={(v) => save(m, v)} onCancel={onClose} />
          : error ? <p role="alert">This handoff can't be edited any more. It may have expired.</p>
          : <p className="sub" role="status">Loading…</p>}
      </DialogContent>
    </Dialog>
  );
}
