import { SlidersHorizontal } from "lucide-react";
import type { FacetName, FacetValue } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { ARRAY_KEYS, type FacetKey, type LibrarySearch } from "@/lib/searchParams";
import { Facets } from "./Facets";

/** Shown instead of the facet rail when the workspace is narrow (container query, see library.css). */
export function FiltersButton({ facets, selected, onToggle }: { facets: Record<FacetName, FacetValue[]> | undefined; selected: LibrarySearch; onToggle: (k: FacetKey, v: string) => void }) {
  const n = ARRAY_KEYS.reduce((a, k) => a + (selected[k]?.length ?? 0), 0);
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button className="filters-btn"><SlidersHorizontal aria-hidden />Filters ({n})</Button>
      </PopoverTrigger>
      <PopoverContent className="facets-pop" align="start" aria-label="Filters">
        <Facets facets={facets} selected={selected} onToggle={onToggle} />
      </PopoverContent>
    </Popover>
  );
}
