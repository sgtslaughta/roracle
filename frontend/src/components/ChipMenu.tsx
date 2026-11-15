import { useState, type ReactNode } from "react";
import { Popover, PopoverAnchor, PopoverContent } from "@/components/ui/popover";

export interface ChipAction { label: string; onSelect: () => void }

/** Right-click (or Shift+F10 / the menu key) on a chip opens its actions. The chip itself stays a plain button. */
export function ChipMenu({ actions, children, label }: { actions: ChipAction[]; children: ReactNode; label: string }) {
  const [open, setOpen] = useState(false);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverAnchor asChild>
        <span className="chipwrap" onContextMenu={(e) => { e.preventDefault(); setOpen(true); }}>{children}</span>
      </PopoverAnchor>
      <PopoverContent className="chipmenu" role="menu" aria-label={label}>
        {actions.map((a) => (
          <button key={a.label} role="menuitem" onClick={() => { setOpen(false); a.onSelect(); }}>{a.label}</button>
        ))}
      </PopoverContent>
    </Popover>
  );
}
