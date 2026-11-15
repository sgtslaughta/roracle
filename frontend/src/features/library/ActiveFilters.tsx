import { X } from "lucide-react";
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
          return (
            <button key={`${k}:${v}`} className="badge-o" title={`${FACET_META[k].label}: ${v}`} onClick={() => onRemove(k, v)} aria-label={`Remove filter ${FACET_META[k].label}: ${v}`}>
              <Icon aria-hidden /><span className="t">{v}</span><X aria-hidden />
            </button>
          );
        }}
      />
      {chips.length > 0 && <Button variant="ghost" onClick={onClear}>Clear filters</Button>}
    </div>
  );
}
