import { Ellipsis } from "lucide-react";
import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { toast } from "sonner";
import { Popover, PopoverAnchor, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

export interface RowAction { label: string; onSelect: () => void; danger?: boolean }

const ROW = "[data-row], li, tr, .trow";
const arrows = (e: KeyboardEvent<HTMLElement>) => {
  const items = [...e.currentTarget.querySelectorAll<HTMLElement>("[role=menuitem]")];
  const at = items.indexOf(document.activeElement as HTMLElement);
  const to = e.key === "Home" ? 0 : e.key === "End" ? items.length - 1
    : e.key === "ArrowDown" ? (at < 0 ? 0 : (at + 1) % items.length)
    : e.key === "ArrowUp" ? (at < 0 ? items.length - 1 : (at - 1 + items.length) % items.length) : -1;
  if (to < 0) return;
  e.preventDefault();
  items[to]?.focus();
};

/**
 * The one actions menu for list rows: a "⋯" button (`kebab`) and right-click anywhere on the row (its closest
 * li/tr/.trow/[data-row]). Pass `kebab={false}` where the row already has inline buttons (right-click only).
 */
export function RowMenu({ actions, label, kebab = true }: { actions: RowAction[]; label: string; kebab?: boolean }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  const row = useRef<Element | null>(null);
  const pt = useRef({ x: 0, y: 0 });
  const virtual = useRef({ getBoundingClientRect: () => new DOMRect(pt.current.x, pt.current.y, 0, 0) });
  useEffect(() => {
    const r = ref.current?.closest(ROW);
    if (!r) return;
    row.current = r;
    const on = (e: Event) => {
      const m = e as MouseEvent;
      // leave the native menu for selected text, links, editable fields and Shift+right-click
      if (m.shiftKey || window.getSelection()?.toString() || (m.target as HTMLElement).closest("a[href], [contenteditable], input, textarea, select, .chipwrap, [role=menu]")) return;
      e.preventDefault();
      pt.current = { x: m.clientX, y: m.clientY };
      setOpen(true);
    };
    r.addEventListener("contextmenu", on);
    return () => r.removeEventListener("contextmenu", on);
  }, []);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <span ref={ref} className={cn("rowmenu", !kebab && "ctx")}>
        {kebab ? (
          <PopoverTrigger asChild>
            <button className="icon-btn rowmenu-btn" aria-label={label} title="Actions"><Ellipsis aria-hidden /></button>
          </PopoverTrigger>
        ) : <PopoverAnchor virtualRef={virtual} />}
      </span>
      <PopoverContent className="chipmenu" role="menu" aria-label={label} onKeyDown={arrows}
        onCloseAutoFocus={kebab ? undefined : (e) => {
          const r = row.current as HTMLElement | null;
          if (!r) return;
          e.preventDefault();
          if (!r.hasAttribute("tabindex")) r.tabIndex = -1;
          r.focus();
        }}>
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
