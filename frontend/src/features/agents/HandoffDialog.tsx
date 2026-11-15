import { Copy, Link as LinkIcon, Terminal } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import { agentPrompt, handoffLink, type HandoffKind, type HandoffManifest } from "@/api/agents";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { useCreateHandoff } from "./api";
import { AUDIENCE_LABEL, HandoffForm, type HandoffValues } from "./HandoffForm";

export interface HandoffSource {
  kind: HandoffKind;
  sourceId?: string;
  docIds: string[];
  title: string;
  goal: string;
  brief: string;
  /** one line under the dialog title: what gets handed over */
  summary: string;
  titlePlaceholder?: string;
}

export async function copyText(text: string, done: string) {
  try { await navigator.clipboard.writeText(text); toast(done); } catch { toast("Copying isn't available here."); }
}

export function HandoffDialog({ source, open, onOpenChange, onCreated }: { source: HandoffSource; open: boolean; onOpenChange: (o: boolean) => void; onCreated?: (m: HandoffManifest) => void }) {
  const create = useCreateHandoff();
  const [made, setMade] = useState<HandoffManifest | null>(null);
  const close = () => onOpenChange(false);

  const submit = (v: HandoffValues) => {
    create.mutate({
      kind: source.kind, ...(source.sourceId && { source_id: source.sourceId }), title: v.title, goal: v.goal, brief: v.brief,
      doc_ids: source.docIds, audience: v.audience, ...(v.shares && { shares: v.shares }), expires_in_days: v.days ?? 7,
    }, {
      onSuccess: (m) => { setMade(m); onCreated?.(m); },
      onError: (e) => toast(e instanceof ApiError && e.status === 404 ? "That source isn't available to hand off." : e instanceof ApiError && e.status === 422 ? e.message : "The handoff couldn't be created. Try again."),
    });
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="w-[min(680px,calc(100vw-32px))]">
        <DialogTitle>Continue in an agent</DialogTitle>
        <DialogDescription>{source.summary}</DialogDescription>
        {made ? <ManifestView m={made} onClose={close} /> : (
          <HandoffForm
            mode="create" initial={source} titlePlaceholder={source.titlePlaceholder} busy={create.isPending}
            submitLabel="Create handoff link" onSubmit={submit} onCancel={close}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}

export function ManifestView({ m, onClose }: { m: HandoffManifest; onClose?: () => void }) {
  return (
    <div className="hlink" aria-live="polite">
      <div className="row2"><LinkIcon aria-hidden /><code>{m.display}</code>
        <span>Expires {new Date(m.expires_at).toLocaleDateString([], { dateStyle: "medium" })}. {AUDIENCE_LABEL[m.audience]}.</span>
      </div>
      <div className="row2">
        <Button onClick={() => void copyText(handoffLink(m.url), "Handoff link copied")}><Copy aria-hidden />Copy link</Button>
        <Button variant="primary" onClick={() => void copyText(agentPrompt(m.display, m.goal), "Agent prompt copied. Paste it into your agent.")}><Terminal aria-hidden />Copy agent prompt</Button>
        {onClose && <Button variant="ghost" onClick={onClose}>Done</Button>}
      </div>
    </div>
  );
}
