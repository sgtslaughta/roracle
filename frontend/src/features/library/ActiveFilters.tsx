import { X } from "lucide-react";
import { toast } from "sonner";
import { ChipMenu } from "@/components/ChipMenu";
import { OverflowList } from "@/components/OverflowList";
import { Button } from "@/components/ui/button";
import { ARRAY_KEYS, type FacetKey, type LibrarySearch, type NotKey } from "@/lib/searchParams";
import { useCollectionName } from "./api";
import { useLabelName } from "@/features/labels/api";
import { FACET_META } from "./FacetRail";

export function ActiveFilters({ search, onRemove, onClear }: { search: LibrarySearch; onRemove: (k: FacetKey | NotKey, v: string) => void; onClear: () => void }) {
  const collName = useCollectionName();
  const labelName = useLabelName();
  const nameOf = (k: FacetKey, v: string) => (k === "collection" ? collName(v) : k === "label" ? labelName(v) : v);
  const chips = ARRAY_KEYS.flatMap((k) => (search[k] ?? []).map((v) => ({ k, v, base: k.replace(/_not$/, "") as FacetKey, not: k.endsWith("_not") })));
  return (
    <div className="active" aria-label="Active filters">
      <OverflowList
        items={chips} max={5} menuLabel="More filters" getLabel={(c) => nameOf(c.base, c.v)}
        footer={<Button variant="ghost" onClick={onClear}>Clear all</Button>}
        renderItem={({ k, v, base, not }) => {
          const Icon = FACET_META[base].icon;
          const label = `${not ? "Not " : ""}${FACET_META[base].label}`;
          const shown = nameOf(base, v);
          return (
            <ChipMenu key={`${k}:${v}`} label={`Actions for filter ${label}: ${shown}`} actions={[
              { label: "Remove filter", onSelect: () => onRemove(k, v) },
              { label: "Copy value", onSelect: () => { void navigator.clipboard?.writeText(shown); toast(`Copied ${shown}`); } },
            ]}>
              <button className="badge-o" title={`${label}: ${shown}`} onClick={() => onRemove(k, v)} aria-label={`Remove filter ${label}: ${shown}`}>
                <Icon aria-hidden /><span className="t">{shown}</span><X aria-hidden />
              </button>
            </ChipMenu>
          );
        }}
      />
    </div>
  );
}
