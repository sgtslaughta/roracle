import { Clock, FileText, FileType2, History, Quote, TextQuote } from "lucide-react";
import type { Bbox, SearchHit } from "@/api/types";
import { toast } from "sonner";
import { ChipMenu } from "@/components/ChipMenu";
import { OverflowList } from "@/components/OverflowList";
import { Badge } from "@/components/ui/badge";
import { highlight } from "./highlight";
import { VoteControl } from "./VoteControl";

export interface ResultCardProps {
  hit: SearchHit;
  terms: string[];
  others: { id: string; title: string }[];
  onOpen: (docId: string, opts?: { page?: number; chunkId?: string; bbox?: Bbox | null }) => void;
  onTag: (tag: string) => void;
}

export function ResultCard({ hit, terms, others, onOpen, onTag }: ResultCardProps) {
  const TypeIcon = hit.file_type === "PDF" ? FileText : FileType2;
  return (
    <li className="result" tabIndex={0} data-doc={hit.doc_id}>
      <VoteControl docId={hit.doc_id} votes={hit.votes} candidates={others.filter((o) => o.id !== hit.doc_id)} />
      <div style={{ minWidth: 0 }}>
        <div className="rtop">
          <div style={{ minWidth: 0 }}>
            <h3><button title={hit.title} onClick={() => onOpen(hit.doc_id)}>{highlight(hit.title, terms)}</button></h3>
            <p className="citeline trunc" title={`${hit.authors.join(", ")}. ${hit.venue ?? ""} ${hit.year}`}>{hit.authors.join(", ")}. {hit.venue && <i>{hit.venue}</i>}{hit.venue && ", "}{hit.year}.</p>
          </div>
          {hit.flag && <span className="flag"><Clock aria-hidden />{hit.flag}</span>}
        </div>
        <div className="hits">
          {hit.passages.map((p) => (
            <button key={p.chunk_id} className="hit" onClick={() => onOpen(hit.doc_id, { page: p.page, chunkId: p.chunk_id, bbox: p.bbox })}>
              <small><TextQuote aria-hidden />Page {p.page}</small>
              {highlight(p.text, terms)}
            </button>
          ))}
        </div>
        <div className="rmeta">
          <Badge kind="type"><TypeIcon aria-hidden />{hit.file_type}</Badge>
          <Badge><History aria-hidden />v{hit.version}</Badge>
          {!!hit.cited_by_count && <Badge kind="cite"><Quote aria-hidden />Cited by {hit.cited_by_count} here</Badge>}
          <OverflowList
            items={hit.tags} max={4} menuLabel="More tags" getLabel={(t) => t} className="ovl-tags"
            renderItem={(t) => (
              <ChipMenu key={t} label={`Actions for tag ${t}`} actions={[
                { label: "Filter by this tag", onSelect: () => onTag(t) },
                { label: "Copy tag", onSelect: () => { void navigator.clipboard?.writeText(t); toast(`Copied #${t}`); } },
              ]}>
                <button className="badge-o tag" title={`#${t}`} onClick={() => onTag(t)}><span className="t">#{t}</span></button>
              </ChipMenu>
            )}
          />
        </div>
      </div>
    </li>
  );
}
