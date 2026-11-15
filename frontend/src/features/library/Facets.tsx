import { Building2, Calendar, File, Folder, Tag, User } from "lucide-react";
import { useState } from "react";
import type { FacetName, FacetValue } from "@/api/types";
import { Checkbox } from "@/components/ui/checkbox";
import type { FacetKey, LibrarySearch } from "@/lib/searchParams";

export const FACET_META: Record<FacetKey, { label: string; icon: typeof Tag }> = {
  collection: { label: "Collection", icon: Folder },
  tags: { label: "Tags", icon: Tag },
  file_type: { label: "File type", icon: File },
  org_unit: { label: "Org unit", icon: Building2 },
  year: { label: "Year", icon: Calendar },
  author: { label: "Author", icon: User },
};
const ORDER = Object.keys(FACET_META) as FacetKey[];
const LIMIT = 8;

/** Values come from the server's facet counts for the current result set; a selected value stays visible at 0. */
export function Facets({ facets, selected, onToggle }: {
  facets: Record<FacetName, FacetValue[]> | undefined;
  selected: LibrarySearch;
  onToggle: (k: FacetKey, v: string) => void;
}) {
  const [open, setOpen] = useState<Set<string>>(new Set());
  return (
    <aside className="facets" aria-label="Filters">
      <p className="fnote">Filters list only what's in these results.</p>
      {ORDER.map((k) => {
        const sel = selected[k] ?? [];
        const counts = new Map((facets?.[k] ?? []).map((f) => [f.value, f.count]));
        sel.forEach((v) => { if (!counts.has(v)) counts.set(v, 0); });
        const vals = [...counts.keys()];
        if (!vals.length) return null;
        const all = open.has(k);
        const { label, icon: Icon } = FACET_META[k];
        return (
          <div className="fgroup" key={k} role="group" aria-label={label}>
            <h4><Icon aria-hidden />{label}</h4>
            {(all ? vals : vals.slice(0, LIMIT)).map((v) => (
              <label key={v}>
                <Checkbox checked={sel.includes(v)} onCheckedChange={() => onToggle(k, v)} aria-label={`${label}: ${v}`} />
                <span className="v">{v}</span>
                <span className="n">{counts.get(v)}</span>
              </label>
            ))}
            {vals.length > LIMIT && (
              <button className="more" onClick={() => setOpen((o) => { const n = new Set(o); if (all) n.delete(k); else n.add(k); return n; })}>
                {all ? "Show fewer" : `Show all ${vals.length}`}
              </button>
            )}
          </div>
        );
      })}
    </aside>
  );
}
