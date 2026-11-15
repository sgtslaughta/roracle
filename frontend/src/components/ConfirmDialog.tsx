import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";

interface Props { open: boolean; onOpenChange: (o: boolean) => void; title: string; children: ReactNode; confirm: string; onConfirm: () => void; busy?: boolean }

/** Small "are you sure" dialog for destructive actions. Cancel has focus first. */
export function ConfirmDialog({ open, onOpenChange, title, children, confirm, onConfirm, busy }: Props) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="w-[min(440px,calc(100vw-32px))]">
        <DialogTitle>{title}</DialogTitle>
        <DialogDescription>{children}</DialogDescription>
        <div className="foot">
          <Button variant="ghost" onClick={() => onOpenChange(false)} autoFocus>Cancel</Button>
          <Button variant="primary" onClick={onConfirm} disabled={busy}>{confirm}</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
