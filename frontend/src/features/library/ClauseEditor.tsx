import type { FacetName, FacetValue } from "@/api/types";
import { useLabelName } from "@/features/labels/api";
import { FACET_KEYS, type FacetKey, type LibrarySearch } from "@/lib/searchParams";
import { useCollectionName } from "./api";
import { FacetGroup } from "./FacetGroup";
import { FACET_META, facetRows } from "./FacetRail";
import { Refine, ScopeSelect } from "./QueryBar";
import type { EditTarget } from "./querySentence";
import { CitingRow, HasFields, IdFields, OwnerField, SignalField } from "./SignalFilters";
import { SortSelect } from "./SortSelect";
import type { useLibraryNav } from "./useLibraryNav";
import { YearFacet } from "./YearFacet";

type Nav = Pick<ReturnType<typeof useLibraryNav>, "toggle" | "patch" | "setSort">;
type Props = { edit: EditTarget; search: LibrarySearch; nav: Nav; facets?: Record<FacetName, FacetValue[]>; onDone: () => void };

function FacetEditor({ k, search, nav, facets }: { k: FacetKey } & Omit<Props, "edit" | "onDone">) {
  const { values, sel, exc, nameOf, opKey } = facetRows(k, facets, search, useCollectionName(), useLabelName());
  return (
    <FacetGroup
      k={k} facet={FACET_META[k]} values={values} selected={sel} excluded={exc} search={search} nameOf={nameOf}
      onToggle={(v, mode) => nav.toggle(k, v, mode)}
      op={opKey ? search[opKey] : undefined} onOp={opKey ? (o) => nav.patch({ [opKey]: o }) : undefined}
    />
  );
}

/** The control for one sentence clause: the same controls as the rail and the query bar, hosted in a popover. */
export function ClauseEditor({ edit, search, nav, facets, onDone }: Props) {
  if ((FACET_KEYS as readonly string[]).includes(edit)) return <FacetEditor k={edit as FacetKey} search={search} nav={nav} facets={facets} />;
  switch (edit) {
    case "q":
      return (
        <div className="cedit-q">
          <label className="sfield"><span>Search in</span><ScopeSelect search={search} nav={nav} /></label>
          <Refine q={search.q ?? ""} onApply={(q) => { nav.patch({ q }); onDone(); }} />
        </div>
      );
    case "year":
      return <YearFacet buckets={facets?.year ?? []} min={search.year_min} max={search.year_max} onCommit={(a, b) => nav.patch({ year_min: a, year_max: b })} />;
    case "added": case "pages": case "votes": case "cited":
      return <SignalField which={edit} search={search} nav={nav} />;
    case "has":
      return <><HasFields search={search} nav={nav} /><IdFields search={search} nav={nav} /></>;
    case "owner":
      return <OwnerField search={search} nav={nav} />;
    case "cites":
      return search.cites ? <CitingRow id={search.cites} onRemove={() => { nav.patch({ cites: undefined }); onDone(); }} /> : null;
    case "sort":
      return <SortSelect value={search.sort} labelClass="cedit-label" onChange={(s) => { nav.setSort(s); onDone(); }} />;
  }
  return null;
}
