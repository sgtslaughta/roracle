import { Clock, FileText, FileType2, History, Quote, TextQuote } from "lucide-react";
import type { Bbox, SearchHit } from "@/api/types";
import { OverflowList } from "@/components/OverflowList";
import { Badge } from "@/components/ui/badge";
import { CiteLine } from "@/components/CiteLine";
import { highlight, safeHighlight } from "./highlight";
import { TagChip } from "./TagChip";
import { useCanRemoveTags } from "./useMe";
import { VoteControl } from "./VoteControl";

export interface ResultCardProps {
  hit: SearchHit;
  terms: string[];
  others: { id: string; title: string }[];
  onOpen: (docId: string, opts?: { page?: number; chunkId?: string; bbox?: Bbox | null }) => void;
  onTag: (tag: string) => void;
}

export function ResultCard({ hit, terms, others, onOpen, onTag }: ResultCardProps) {
  const canRemove = useCanRemoveTags(hit.owner_id);
  const TypeIcon = hit.file_type === "PDF" ? FileText : FileType2;
  return (
    <li className="result" tabIndex={0} data-doc={hit.doc_id}>
      <VoteControl docId={hit.doc_id} votes={hit.votes} candidates={others.filter((o) => o.id !== hit.doc_id)} />
      <div style={{ minWidth: 0 }}>
        <div className="rtop">
          <div style={{ minWidth: 0 }}>
            <h3><button title={hit.title} onClick={() => onOpen(hit.doc_id)}>{highlight(hit.title, terms)}</button></h3>
            <CiteLine authors={hit.authors} venue={hit.venue} year={hit.year} />
          </div>
          {hit.flag && <span className="flag"><Clock aria-hidden />{hit.flag}</span>}
        </div>
        <div className="hits">
          {hit.passages.map((p) => (
            <button key={p.chunk_id} className="hit" onClick={() => onOpen(hit.doc_id, { page: p.page, chunkId: p.chunk_id, bbox: p.bbox })}>
              <small><TextQuote aria-hidden />Page {p.page}</small>
              {p.highlight ? safeHighlight(p.highlight) : p.text}
            </button>
          ))}
        </div>
        <div className="rmeta">
          <Badge kind="type"><TypeIcon aria-hidden />{hit.file_type}</Badge>
          <Badge><History aria-hidden />v{hit.version}</Badge>
          {!!hit.cited_by_count && <Badge kind="cite"><Quote aria-hidden />Cited by {hit.cited_by_count} here</Badge>}
          <OverflowList
            items={hit.tags} max={4} menuLabel="More tags" getLabel={(t) => t} className="ovl-tags"
            renderItem={(t) => <TagChip key={t} docId={hit.doc_id} tag={t} canRemove={canRemove} onFilter={() => onTag(t)} />}
          />
        </div>
      </div>
    </li>
  );
}
