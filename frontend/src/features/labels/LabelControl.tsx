import { Pencil } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { errorText } from "@/api/client";
import type { Label } from "@/api/types";
import { LabelPicker } from "@/components/LabelPicker";
import { SensitivityBadge } from "@/components/SensitivityBadge";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { useMe } from "@/features/library/useMe";
import { useLabels } from "./api";

/** Badge plus (when `onApply` is given) a "Change label" dialog. `current` null = nothing set. */
export function LabelControl({ title, current, floor, onApply, pending, error, reset, hideBadge }: {
  title: string; current: Label | null; hideBadge?: boolean; floor?: Label; pending?: boolean; error?: string | null; reset?: () => void;
  onApply?: (labelId: string, reason: string | undefined, done: () => void) => void;
}) {
  const [open, setOpen] = useState(false);
  const labels = useLabels().data ?? [];
  const admin = !!useMe().data?.is_admin;
  return (
    <>
      {current && !hideBadge && <SensitivityBadge label={current} />}
      {onApply && labels.length > 0 && (
        <button className="icon-btn" aria-label={`Change label of ${title}`} title="Change label" onClick={() => { reset?.(); setOpen(true); }}><Pencil aria-hidden /></button>
      )}
      {open && onApply && (
        <Dialog open onOpenChange={setOpen}>
          <DialogContent className="w-[min(440px,calc(100vw-32px))]">
            <DialogTitle>Change label</DialogTitle>
            <DialogDescription>{admin ? "Admins can set any label." : "You can keep or raise the label; only admins can lower it."} “{title}”.</DialogDescription>
            <LabelPicker labels={labels} current={current} floor={floor} isAdmin={admin} pending={pending} error={error}
              onApply={(id, reason) => onApply(id, reason, () => { setOpen(false); toast("Label updated."); })} />
          </DialogContent>
        </Dialog>
      )}
    </>
  );
}

export const labelError = errorText;
