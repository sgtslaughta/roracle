import { useState } from "react";
import { toast } from "sonner";
import { errorText } from "@/api/client";
import type { AdminLabel } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { useCreateLabel, usePatchLabel } from "./labelsApi";

export const GATE_TEXT = {
  exclude_llm: "Kept out of AI answers",
  block_agents: "Hidden from agents",
  block_export: "No download or export",
  reason_on_lower: "Reason needed to lower",
} as const;
export type GateKey = keyof typeof GATE_TEXT;
const HEX = /^#[0-9a-fA-F]{6}$/;

/** Create (no `label`) or edit name, colour, description and default. Gates are toggled in the list so the API's rule errors show next to them. */
export function LabelForm({ label, top, onClose }: { label?: AdminLabel; top?: AdminLabel; onClose: () => void }) {
  const create = useCreateLabel(), patch = usePatchLabel();
  const [name, setName] = useState(label?.name ?? "");
  const [colour, setColour] = useState(label?.colour ?? "#496580");
  const [description, setDescription] = useState(label?.description ?? "");
  const [isDefault, setDefault] = useState(label?.is_default ?? false);
  const [error, setError] = useState<string | null>(null);
  const ok = !!name.trim() && name.trim().length <= 40 && HEX.test(colour);
  const opts = { onSuccess: () => { toast(label ? "Label saved." : "Label created."); onClose(); }, onError: (e: unknown) => setError(errorText(e)) };
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="w-[min(480px,calc(100vw-32px))]">
        <DialogTitle>{label ? `Edit ${label.name}` : "New label"}</DialogTitle>
        <DialogDescription>{label ? "Gates are switched in the list." : "A new label becomes the most sensitive and starts with the same gates as the one below it (gates can only grow with sensitivity). Reorder it in the list."}</DialogDescription>
        <form className="lform" onSubmit={(e) => {
          e.preventDefault(); setError(null);
          const body = { name: name.trim(), colour, description: description.trim(), ...(isDefault !== (label?.is_default ?? false) || !label ? { is_default: isDefault } : {}) };
          if (label) patch.mutate({ id: label.id, ...body }, opts);
          else create.mutate({ ...body, rank: (top?.rank ?? -1) + 1, gates: top?.gates }, opts);
        }}>
          <label>Name<input className="inp" value={name} maxLength={40} autoFocus onChange={(e) => setName(e.target.value)} /></label>
          <div className="agent-row">
            <label>Colour<input type="color" aria-label="Colour picker" value={HEX.test(colour) ? colour : "#496580"} onChange={(e) => setColour(e.target.value)} /></label>
            <label>Hex<input className="inp" value={colour} maxLength={7} aria-invalid={!HEX.test(colour)} onChange={(e) => setColour(e.target.value)} /></label>
          </div>
          <label>Description (shown in the badge tooltip)<textarea className="ta short" maxLength={500} value={description} onChange={(e) => setDescription(e.target.value)} /></label>
          <label className="ck"><input type="checkbox" checked={isDefault} onChange={(e) => setDefault(e.target.checked)} />Default label for new documents</label>
          {error && <p className="chipin-err" role="alert">{error}</p>}
          <div className="foot">
            <Button variant="ghost" type="button" onClick={onClose}>Cancel</Button>
            <Button variant="primary" type="submit" disabled={!ok || create.isPending || patch.isPending}>{label ? "Save" : "Create"}</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
