import { ArrowDown, ArrowUp } from "lucide-react";
import type { SearchHit } from "@/api/types";
import { docTitle } from "@/lib/docTitle";
import type { Column, Sort } from "@/lib/searchParams";
import { COLUMN_LABEL } from "./ColumnPicker";
import { firstAuthor, netVotes } from "./CompactRow";

const SORT_OF: Record<Column, Sort> = { title: "title", authors: "first_author", year: "published_desc", venue: "venue", cited: "cited", votes: "votes", added: "newest" };

const CELL: Record<Exclude<Column, "title">, (h: SearchHit) => string | number> = {
  authors: (h) => firstAuthor(h.authors),
  year: (h) => h.year ?? "",
  venue: (h) => h.venue ?? "",
  cited: (h) => h.cited_by_count,
  votes: (h) => netVotes(h),
  added: (h) => h.added_at.slice(0, 10),
};

export interface ResultTableProps {
  hits: SearchHit[];
  columns: readonly Column[];
  sort: Sort | undefined;
  onSort: (s: Sort) => void;
  onOpen: (docId: string) => void;
}

export function ResultTable({ hits, columns, sort, onSort, onOpen }: ResultTableProps) {
  const cols = columns.includes("title") ? columns : (["title", ...columns] as Column[]);
  const click = (c: Column) => onSort(c === "year" && sort === "published_desc" ? "published_asc" : SORT_OF[c]);
  const active = (c: Column) => (c === "year" ? sort === "published_desc" || sort === "published_asc" : sort === SORT_OF[c]);
  return (
    <div className="rtable-wrap">
      <table className="rtable">
        <thead>
          <tr>
            {cols.map((c) => (
              <th key={c} scope="col" className={c === "title" ? "tcol" : undefined} aria-sort={active(c) ? (sort === "published_desc" || sort === "newest" || sort === "votes" || sort === "cited" ? "descending" : "ascending") : "none"}>
                <button type="button" onClick={() => click(c)}>
                  {COLUMN_LABEL[c]}
                  {active(c) && (sort === "published_asc" || sort === "title" || sort === "first_author" || sort === "venue" ? <ArrowUp aria-hidden /> : <ArrowDown aria-hidden />)}
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {hits.map((h) => (
            <tr key={h.doc_id} data-doc={h.doc_id}>
              {cols.map((c) => c === "title"
                ? <th key={c} scope="row" className="tcol"><button title={docTitle(h)} onClick={() => onOpen(h.doc_id)}>{docTitle(h)}</button></th>
                : <td key={c} className={c === "authors" || c === "venue" ? "tx" : "tn"}>{CELL[c](h)}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
