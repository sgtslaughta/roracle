import { Ellipsis } from "lucide-react";
import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { toast } from "sonner";
import { Popover, PopoverAnchor, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

export interface RowAction { label: string; onSelect: () => void; danger?: boolean }

const ROW = "[data-row], li, tr, .trow";
const arrows = (e: KeyboardEvent<HTMLElement>) => {
  const d = e.key === "ArrowDown" ? 1 : e.key === "ArrowUp" ? -1 : 0;
  if (!d) return;
  e.preventDefault();
  const items = [...e.currentTarget.querySelectorAll<HTMLElement>("[role=menuitem]")];
  items[(items.indexOf(document.activeElement as HTMLElement) + d + items.length) % items.length]?.focus();
};

/**
 * The one actions menu for list rows: a "⋯" button (`kebab`) and right-click anywhere on the row (its closest
 * li/tr/.trow/[data-row]). Pass `kebab={false}` where the row already has inline buttons (right-click only).
 */
export function RowMenu({ actions, label, kebab = true }: { actions: RowAction[]; label: string; kebab?: boolean }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    const row = ref.current?.closest(ROW);
    if (!row) return;
    const on = (e: Event) => {
      if ((e.target as HTMLElement).closest("input, textarea, select, .chipwrap, [role=menu]")) return;
      e.preventDefault();
      setOpen(true);
    };
    row.addEventListener("contextmenu", on);
    return () => row.removeEventListener("contextmenu", on);
  }, []);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <span ref={ref} className={cn("rowmenu", !kebab && "ctx")}>
        {kebab ? (
          <PopoverTrigger asChild>
            <button className="icon-btn rowmenu-btn" aria-label={label} title="Actions"><Ellipsis aria-hidden /></button>
          </PopoverTrigger>
        ) : <PopoverAnchor asChild><span /></PopoverAnchor>}
      </span>
      <PopoverContent className="chipmenu" role="menu" aria-label={label} onKeyDown={arrows}>
        {actions.map((a) => (
          <button key={a.label} role="menuitem" className={a.danger ? "danger" : undefined} onClick={() => { setOpen(false); a.onSelect(); }}>{a.label}</button>
        ))}
      </PopoverContent>
    </Popover>
  );
}

/** A "Copy …" action: clipboard write plus a confirmation toast. */
export const copyAction = (label: string, text: string): RowAction => ({
  label, onSelect: () => { void navigator.clipboard?.writeText(text); toast("Copied"); },
});
