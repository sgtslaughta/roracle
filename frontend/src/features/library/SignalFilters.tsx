import { useEffect, useId, useState } from "react";
import { X } from "lucide-react";
import { ApiError } from "@/api/client";
import type { LibrarySearch } from "@/lib/searchParams";
import { useDoc } from "@/features/reader/api";
import { RangeFields } from "./RangeFields";
import type { useLibraryNav } from "./useLibraryNav";

type Nav = Pick<ReturnType<typeof useLibraryNav>, "patch">;

function CountField({ label, value, min, onCommit }: { label: string; value?: number; min: number; onCommit: (n?: number) => void }) {
  const [t, setT] = useState(value == null ? "" : String(value));
  useEffect(() => setT(value == null ? "" : String(value)), [value]);
  const commit = () => {
    const n = t.trim() === "" ? undefined : Number(t);
    if (n !== undefined && (!Number.isInteger(n) || n < min)) return setT(value == null ? "" : String(value));
    if (n !== value) onCommit(n);
  };
  return (
    <label className="sfield">
      <span>{label}</span>
      <input type="number" min={min} value={t} onChange={(e) => setT(e.target.value)} onBlur={commit} onKeyDown={(e) => e.key === "Enter" && commit()} />
    </label>
  );
}

function CitingRow({ id, onRemove }: { id: string; onRemove: () => void }) {
  const doc = useDoc(id);
  const text = doc.data ? `Citing ${doc.data.title}` : doc.error instanceof ApiError && doc.error.status === 404 ? "Citing an unavailable paper" : "Citing…";
  return (
    <div className="scite">
      <span className="v" title={text}>{text}</span>
      <button type="button" className="fx" aria-label="Remove citing filter" onClick={onRemove}><X aria-hidden /></button>
    </div>
  );
}

const OWNERS = [[undefined, "Everyone's"], ["mine", "Mine"], ["shared", "Shared with me"]] as const;
const TRI = [["has_doi", "Has a DOI"], ["has_abstract", "Has an abstract"], ["has_refs", "Has references"]] as const;

/** Date added, page count, votes, cited-by, has-field selects, owner and the cites scope. */
export function SignalFilters({ search, nav }: { search: LibrarySearch; nav: Nav }) {
  const gid = useId();
  return (
    <div className="fgroup signals">
      <RangeFields label="Date added" kind="date" min={search.added_min} max={search.added_max} onCommit={(a, b) => nav.patch({ added_min: a as string | undefined, added_max: b as string | undefined })} />
      <RangeFields label="Page count" kind="number" min={search.pages_min} max={search.pages_max} onCommit={(a, b) => nav.patch({ pages_min: a as number | undefined, pages_max: b as number | undefined })} />
      <CountField label="At least N votes" value={search.votes_min} min={Number.MIN_SAFE_INTEGER} onCommit={(n) => nav.patch({ votes_min: n })} />
      <CountField label="Cited by at least N library papers" value={search.cited_min} min={1} onCommit={(n) => nav.patch({ cited_min: n })} />
      {TRI.map(([k, label]) => (
        <label className="sfield" key={k}>
          <span>{label}</span>
          <select value={search[k] ?? ""} onChange={(e) => nav.patch({ [k]: e.target.value || undefined })}>
            <option value="">Any</option><option value="yes">Yes</option><option value="no">No</option>
          </select>
        </label>
      ))}
      <div className="fop" role="radiogroup" aria-label="Owner">
        {OWNERS.map(([o, l]) => (
          <label key={l}><input type="radio" name={`${gid}-owner`} checked={search.owner === o} onChange={() => nav.patch({ owner: o })} />{l}</label>
        ))}
      </div>
      {search.cites && <CitingRow id={search.cites} onRemove={() => nav.patch({ cites: undefined })} />}
    </div>
  );
}
