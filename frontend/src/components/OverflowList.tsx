import { useState, type ReactNode } from "react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

export interface OverflowListProps<T> {
  items: T[];
  /** how many items stay inline */
  max: number;
  renderItem: (item: T, index: number) => ReactNode;
  /** accessible name for the overflow menu, e.g. "More tags" */
  menuLabel: string;
  /** text used by the menu's filter input */
  getLabel?: (item: T) => string;
  /** inline = one line of chips; stack = vertical list (facet values, sidebar entries) */
  layout?: "inline" | "stack";
  /** trigger text; default `+K` */
  moreLabel?: (hidden: number) => string;
  /** always show the filter input (facets), else only when the menu holds more than 10 items */
  searchable?: boolean;
  /** extra menu content below the list, e.g. a "Clear all" button */
  footer?: ReactNode;
  className?: string;
  triggerClassName?: string;
}

const FILTER_THRESHOLD = 10;

/** Caps an unbounded list: `max` items inline, the rest behind a scrollable (max 320px) popover with an optional filter. */
export function OverflowList<T>({
  items, max, renderItem, menuLabel, getLabel, layout = "inline", moreLabel, searchable, footer, className, triggerClassName,
}: OverflowListProps<T>) {
  const [q, setQ] = useState("");
  const shown = items.slice(0, max);
  const hidden = items.length - shown.length;
  // stack layout (facets) lists everything in the menu so it can be searched; inline lists only the rest
  const menuItems = layout === "stack" ? items : items.slice(max);
  const needle = q.trim().toLowerCase();
  const visible = needle && getLabel ? menuItems.filter((it) => getLabel(it).toLowerCase().includes(needle)) : menuItems;
  const showFilter = !!getLabel && (searchable || menuItems.length > FILTER_THRESHOLD);

  return (
    <div className={cn("ovl", layout === "stack" && "ovl-stack", className)}>
      {shown.map((it, i) => renderItem(it, i))}
      {hidden > 0 && (
        <Popover onOpenChange={(o) => !o && setQ("")}>
          <PopoverTrigger asChild>
            <button className={cn("badge-o ovl-more", triggerClassName)} aria-label={moreLabel ? undefined : `${menuLabel}: ${hidden} more`} title={menuLabel}>
              {moreLabel ? moreLabel(hidden) : `+${hidden}`}
            </button>
          </PopoverTrigger>
          <PopoverContent className="ovl-menu" aria-label={menuLabel}>
            {showFilter && (
              <input className="ovl-filter" type="search" placeholder="Filter" aria-label={`Filter ${menuLabel.toLowerCase()}`} value={q} onChange={(e) => setQ(e.target.value)} />
            )}
            <div className="ovl-scroll">
              {visible.map((it, i) => renderItem(it, i))}
              {!visible.length && <p className="ovl-empty">No matches</p>}
            </div>
            {footer}
          </PopoverContent>
        </Popover>
      )}
    </div>
  );
}
