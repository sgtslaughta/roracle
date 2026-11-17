import type { SearchHit } from "@/api/types";
import { docTitle } from "@/lib/docTitle";

export const firstAuthor = (authors: string[]) => (authors.length ? authors[0] + (authors.length > 1 ? " et al." : "") : "");
export const netVotes = (h: SearchHit) => h.votes.up - h.votes.down;

export function CompactRow({ hit, onOpen }: { hit: SearchHit; onOpen: (docId: string) => void }) {
  const title = docTitle(hit);
  const meta = [firstAuthor(hit.authors), hit.year, hit.venue].filter((x) => x !== null && x !== "" && x !== undefined);
  return (
    <li className="result compact" data-doc={hit.doc_id}>
      <button className="ctitle" title={title} onClick={() => onOpen(hit.doc_id)}>{title}</button>
      <span className="cmeta trunc">{meta.join(" · ")}</span>
      <span className="cnum" title="Cited by">Cited {hit.cited_by_count}</span>
      <span className="cnum" title="Net votes">{netVotes(hit) > 0 ? "+" : ""}{netVotes(hit)} votes</span>
    </li>
  );
}
