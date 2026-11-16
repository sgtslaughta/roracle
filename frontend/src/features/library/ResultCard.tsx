import { Clock, FileText, FileType2, History, MessageSquareQuote, Quote, TextQuote } from "lucide-react";
import type { CommunityNote } from "@/api/notes";
import type { Bbox, SearchHit } from "@/api/types";
import { OverflowList } from "@/components/OverflowList";
import { Badge } from "@/components/ui/badge";
import { CiteLine } from "@/components/CiteLine";
import { notesUi } from "@/features/notes/api";
import { KindPill } from "@/features/notes/NoteCard";
import { openDoc, setView } from "@/features/reader/useReaderStore";
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
          <CommunityNotes docId={hit.doc_id} notes={hit.notes ?? []} />
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

export const NOTES_INLINE = 2;

/** Open the document's Notes tab in the reader; `flash` scrolls to and highlights one note. */
function openNotes(docId: string, page?: number | null, flash?: string) {
  openDoc(docId, page ? { page } : {});
  setView("notes");
  if (flash) notesUi.set((s) => ({ ...s, flash }));
}

/** Community notes on a hit: plain text (never markdown), up to 2 inline, the rest via the reader's Notes tab. */
function CommunityNotes({ docId, notes }: { docId: string; notes: CommunityNote[] }) {
  const more = notes.length - NOTES_INLINE;
  return (
    <>
      {notes.slice(0, NOTES_INLINE).map((n) => (
        <button key={n.id} className="hit cnote" onClick={() => openNotes(docId, n.page, n.id)}>
          <small>
            <MessageSquareQuote aria-hidden />Community note by {n.author.username || "a reader"}
            <KindPill kind={n.kind} />
            {n.page != null && <span>· Page {n.page}</span>}
          </small>
          <span className="cnote-body">{n.body.slice(0, 500)}</span>
        </button>
      ))}
      {more > 0 && <button className="btn-link" onClick={() => openNotes(docId)}>+{more} {more === 1 ? "note" : "notes"}</button>}
    </>
  );
}
