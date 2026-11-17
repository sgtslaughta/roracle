import { SlidersHorizontal } from "lucide-react";
import type { FacetName, FacetValue } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { ARRAY_KEYS, type LibrarySearch } from "@/lib/searchParams";
import { FacetRail } from "./FacetRail";
import type { useLibraryNav } from "./useLibraryNav";

/** Shown instead of the facet rail when the workspace is narrow (container query, see library.css). */
export function FiltersButton({ facets, search, nav }: { facets: Record<FacetName, FacetValue[]> | undefined; search: LibrarySearch; nav: Pick<ReturnType<typeof useLibraryNav>, "toggle" | "patch"> }) {
  const n = ARRAY_KEYS.reduce((a, k) => a + (search[k]?.length ?? 0), 0);
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button className="filters-btn"><SlidersHorizontal aria-hidden />Filters ({n})</Button>
      </PopoverTrigger>
      <PopoverContent className="facets-pop" align="start" aria-label="Filters">
        <FacetRail facets={facets} search={search} nav={nav} />
      </PopoverContent>
    </Popover>
  );
}
