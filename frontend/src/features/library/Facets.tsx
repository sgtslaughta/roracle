import { Building2, Calendar, File, Folder, Tag, User, ShieldHalf } from "lucide-react";
import { OverflowList } from "@/components/OverflowList";
import type { FacetName, FacetValue } from "@/api/types";
import { Checkbox } from "@/components/ui/checkbox";
import { useCollectionName } from "./api";
import { useLabelName } from "@/features/labels/api";
import type { FacetKey, LibrarySearch } from "@/lib/searchParams";

export const FACET_META: Record<FacetKey, { label: string; icon: typeof Tag }> = {
  collection: { label: "Collection", icon: Folder },
  tags: { label: "Tags", icon: Tag },
  file_type: { label: "File type", icon: File },
  org_unit: { label: "Org unit", icon: Building2 },
  year: { label: "Year", icon: Calendar },
  author: { label: "Author", icon: User },
  label: { label: "Sensitivity", icon: ShieldHalf },
};
const ORDER = Object.keys(FACET_META) as FacetKey[];
const LIMIT = 8;

/** Values come from the server's facet counts for the current result set; a selected value stays visible at 0. */
export function Facets({ facets, selected, onToggle, approximate }: {
  approximate?: boolean;
  facets: Record<FacetName, FacetValue[]> | undefined;
  selected: LibrarySearch;
  onToggle: (k: FacetKey, v: string) => void;
}) {
  const collName = useCollectionName();
  const labelName = useLabelName();
  return (
    <aside className="facets" aria-label="Filters">
      <p className="fnote">Filters list only what's in these results.</p>
      {approximate && <p className="fnote">Counts cover the 200 newest documents.</p>}
      {ORDER.map((k) => {
        const sel = selected[k] ?? [];
        const counts = new Map((facets?.[k] ?? []).map((f) => [f.value, f.count]));
        const labels = new Map((facets?.[k] ?? []).map((f) => [f.value, f.label ?? f.value]));
        const ranks = new Map((facets?.[k] ?? []).map((f) => [f.value, typeof f.label === "object" ? f.label.rank : 0]));
        const nameOf = (v: string) => {
          const l = labels.get(v);
          return typeof l === "string" ? l : l ? l.name : k === "collection" ? collName(v) : k === "label" ? labelName(v) : v;
        };
        sel.forEach((v) => { if (!counts.has(v)) counts.set(v, 0); });
        const vals = [...counts.keys()];
        if (k === "label") vals.sort((a, b) => (ranks.get(a) ?? 0) - (ranks.get(b) ?? 0)); // most sensitive last
        if (!vals.length) return null;
        const { label, icon: Icon } = FACET_META[k];
        return (
          <div className="fgroup" key={k} role="group" aria-label={label}>
            <h4><Icon aria-hidden />{label}</h4>
            <OverflowList
              items={vals} max={LIMIT} layout="stack" searchable getLabel={nameOf} menuLabel={`All ${label.toLowerCase()} values`}
              moreLabel={(h) => `Show all ${h + LIMIT}`} triggerClassName="more"
              renderItem={(v) => (
                <label key={v} title={nameOf(v)}>
                  <Checkbox checked={sel.includes(v)} onCheckedChange={() => onToggle(k, v)} aria-label={`${label}: ${nameOf(v)}`} />
                  <span className="v">{nameOf(v)}</span>
                  <span className="n">{counts.get(v)}</span>
                </label>
              )}
            />
          </div>
        );
      })}
    </aside>
  );
}
