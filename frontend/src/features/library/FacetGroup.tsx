import { useId, useState, type ComponentType } from "react";
import { MinusCircle } from "lucide-react";
import { OverflowList } from "@/components/OverflowList";
import { Checkbox } from "@/components/ui/checkbox";
import type { FacetKey, LibrarySearch } from "@/lib/searchParams";
import { useFacetFind, type FindName } from "./api";

export type FacetRow = { value: string; name: string; count: number };
const LIMIT = 8;
const FINDABLE: readonly string[] = ["author", "venue", "tags"];

type Props = {
  k: FacetKey;
  facet: { label: string; icon: ComponentType<{ "aria-hidden"?: boolean }> };
  values: FacetRow[];
  selected: string[];
  excluded: string[];
  search: LibrarySearch;
  onToggle: (v: string, mode: "include" | "exclude") => void;
  /** only author and tags: switch between any and all */
  onOp?: (op: "and" | undefined) => void;
  op?: "and";
  nameOf?: (v: string) => string;
};

/** One facet: include checkbox, count and exclude toggle per value; optional match any/all and find box. */
export function FacetGroup({ k, facet: { label, icon: Icon }, values, selected, excluded, search, onToggle, onOp, op, nameOf }: Props) {
  const [find, setFind] = useState("");
  const gid = useId();
  const findable = FINDABLE.includes(k);
  const found = useFacetFind(findable ? (k as FindName) : "author", findable ? find : "", search);
  const finding = findable && find.trim() !== "";
  const rows: FacetRow[] = finding ? (found.data?.values ?? []).map((f) => ({ value: f.value, name: nameOf?.(f.value) ?? f.value, count: f.count })) : values;
  if (!values.length && !findable) return null;

  const row = (r: FacetRow) => {
    const ex = excluded.includes(r.value);
    return (
      <div className="frow" key={r.value} title={r.name} data-excluded={ex || undefined}>
        <label>
          <Checkbox checked={selected.includes(r.value)} onCheckedChange={() => onToggle(r.value, "include")} aria-label={`Include ${label}: ${r.name}`} />
          <span className="v">{r.name}</span>
          <span className="n">{r.count}</span>
        </label>
        <button type="button" className="fx" aria-pressed={ex} aria-label={`Exclude ${label}: ${r.name}`} onClick={() => onToggle(r.value, "exclude")}>
          <MinusCircle aria-hidden />
        </button>
      </div>
    );
  };

  return (
    <div className="fgroup" role="group" aria-label={label}>
      <h4><Icon aria-hidden />{label}</h4>
      {findable && (
        <input className="ffind" type="search" placeholder={`Find ${label.toLowerCase()}`} aria-label={`Find ${label.toLowerCase()}`} value={find} onChange={(e) => setFind(e.target.value)} />
      )}
      {onOp && selected.length >= 2 && (
        <div className="fop" role="radiogroup" aria-label="Match any / Match all">
          {([undefined, "and"] as const).map((o) => (
            <label key={o ?? "any"}>
              <input type="radio" name={`${gid}-op`} checked={op === o} onChange={() => onOp(o)} />
              {o ? "Match all" : "Match any"}
            </label>
          ))}
        </div>
      )}
      {finding ? (
        <div className="ovl ovl-stack">{rows.length ? rows.map(row) : <p className="fnote">{found.isFetching ? "Searching…" : "No matches"}</p>}</div>
      ) : (
        <OverflowList
          items={rows} max={LIMIT} layout="stack" searchable getLabel={(r) => r.name} menuLabel={`All ${label.toLowerCase()} values`}
          moreLabel={(h) => `Show all ${h + LIMIT}`} triggerClassName="more" renderItem={row}
        />
      )}
    </div>
  );
}
