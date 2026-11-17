import { BookOpen, Building2, File, Folder, ShieldHalf, Tag, User } from "lucide-react";
import type { FacetName, FacetValue } from "@/api/types";
import { useLabelName } from "@/features/labels/api";
import type { FacetKey, LibrarySearch } from "@/lib/searchParams";
import { useCollectionName } from "./api";
import { FacetGroup, type FacetRow } from "./FacetGroup";
import type { useLibraryNav } from "./useLibraryNav";

/** Rail order. */
export const FACET_META: Record<FacetKey, { label: string; icon: typeof Tag }> = {
  collection: { label: "Collection", icon: Folder },
  tags: { label: "Tags", icon: Tag },
  author: { label: "Author", icon: User },
  venue: { label: "Venue", icon: BookOpen },
  file_type: { label: "File type", icon: File },
  org_unit: { label: "Org unit", icon: Building2 },
  label: { label: "Sensitivity", icon: ShieldHalf },
};
const ORDER = Object.keys(FACET_META) as FacetKey[];

type Nav = Pick<ReturnType<typeof useLibraryNav>, "toggle" | "patch">;

/** Values come from the server's facet counts; selected and excluded values stay visible at 0. */
export function FacetRail({ facets, search, nav }: { facets: Record<FacetName, FacetValue[]> | undefined; search: LibrarySearch; nav: Nav }) {
  const collName = useCollectionName();
  const labelName = useLabelName();
  return (
    <aside className="facets" aria-label="Filters">
      <p className="fnote">{search.q ? "Counts cover these results." : "Counts cover everything you can see."}</p>
      {ORDER.map((k) => {
        const fv = facets?.[k] ?? [];
        const labels = new Map(fv.map((f) => [f.value, f.label]));
        const ranks = new Map(fv.map((f) => [f.value, typeof f.label === "object" ? f.label.rank : 0]));
        const nameOf = (v: string) => {
          const l = labels.get(v);
          return typeof l === "string" ? l : l ? l.name : k === "collection" ? collName(v) : k === "label" ? labelName(v) : v;
        };
        const sel = search[k] ?? [];
        const exc = search[`${k}_not`] ?? [];
        const counts = new Map(fv.map((f) => [f.value, f.count]));
        [...sel, ...exc].forEach((v) => { if (!counts.has(v)) counts.set(v, 0); });
        const vals = [...counts.keys()];
        if (k === "label") vals.sort((a, b) => (ranks.get(a) ?? 0) - (ranks.get(b) ?? 0)); // most sensitive last
        const values: FacetRow[] = vals.map((v) => ({ value: v, name: nameOf(v), count: counts.get(v) ?? 0 }));
        const opKey = k === "author" ? "author_op" : k === "tags" ? "tags_op" : undefined;
        return (
          <FacetGroup
            key={k} k={k} facet={FACET_META[k]} values={values} selected={sel} excluded={exc} search={search} nameOf={nameOf}
            onToggle={(v, mode) => nav.toggle(k, v, mode)}
            op={opKey ? search[opKey] : undefined} onOp={opKey ? (o) => nav.patch({ [opKey]: o }) : undefined}
          />
        );
      })}
    </aside>
  );
}
