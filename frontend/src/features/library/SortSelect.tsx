import { useId } from "react";
import { SORTS, type LibrarySearch, type Sort } from "@/lib/searchParams";

const SORT_LABEL: Record<Sort, string> = {
  relevance: "Relevance", newest: "Recently added", votes: "Most useful", title: "Title", published_desc: "Newest published", published_asc: "Oldest published",
  cited: "Most cited", pages: "Shortest", venue: "Venue", author: "Author",
};

export function SortSelect({ value, onChange, labelClass = "vis" }: { value: LibrarySearch["sort"]; onChange: (s: Sort) => void; labelClass?: string }) {
  const id = useId();
  return (
    <span className="sortpair"><label className={labelClass} htmlFor={id}>Sort</label>
      <select id={id} className="sel" value={value ?? "relevance"} onChange={(e) => onChange(e.target.value as Sort)}>
        {SORTS.map((s) => <option key={s} value={s}>{SORT_LABEL[s]}</option>)}
      </select></span>
  );
}
