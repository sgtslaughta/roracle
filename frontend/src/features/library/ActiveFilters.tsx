import { X } from "lucide-react";
import { toast } from "sonner";
import { ChipMenu } from "@/components/ChipMenu";
import { OverflowList } from "@/components/OverflowList";
import { Button } from "@/components/ui/button";
import { ARRAY_KEYS, type FacetKey, type LibrarySearch } from "@/lib/searchParams";
import { FACET_META } from "./Facets";

export function ActiveFilters({ search, onRemove, onClear }: { search: LibrarySearch; onRemove: (k: FacetKey, v: string) => void; onClear: () => void }) {
  const chips = ARRAY_KEYS.flatMap((k) => (search[k] ?? []).map((v) => ({ k, v })));
  return (
    <div className="active" aria-label="Active filters">
      <OverflowList
        items={chips} max={5} menuLabel="More filters" getLabel={(c) => c.v}
        footer={<Button variant="ghost" onClick={onClear}>Clear all</Button>}
        renderItem={({ k, v }) => {
          const Icon = FACET_META[k].icon;
          const label = FACET_META[k].label;
          return (
            <ChipMenu key={`${k}:${v}`} label={`Actions for filter ${label}: ${v}`} actions={[
              { label: "Remove filter", onSelect: () => onRemove(k, v) },
              { label: "Copy value", onSelect: () => { void navigator.clipboard?.writeText(v); toast(`Copied ${v}`); } },
            ]}>
              <button className="badge-o" title={`${label}: ${v}`} onClick={() => onRemove(k, v)} aria-label={`Remove filter ${label}: ${v}`}>
                <Icon aria-hidden /><span className="t">{v}</span><X aria-hidden />
              </button>
            </ChipMenu>
          );
        }}
      />
    </div>
  );
}
