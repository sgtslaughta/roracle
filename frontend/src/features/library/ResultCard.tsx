import { useState } from "react";
import { copyAction, RowMenu } from "@/components/RowMenu";
import { BookOpen, CalendarPlus, Clock, FileText, FileType2, History, ListTree, MessageSquareQuote, Quote, Sparkles, TextQuote } from "lucide-react";
import type { CommunityNote } from "@/api/notes";
import type { Bbox, SearchHit } from "@/api/types";
import { OverflowList } from "@/components/OverflowList";
import { Badge } from "@/components/ui/badge";
import { SensitivityBadge } from "@/components/SensitivityBadge";
import { CiteLine } from "@/components/CiteLine";
import { notesUi } from "@/features/notes/api";
import { KindPill } from "@/features/notes/NoteCard";
import { openDoc, setView } from "@/features/reader/useReaderStore";
import { highlight, safeHighlight } from "./highlight";
import { TagChip } from "./TagChip";
import { docTitle } from "@/lib/docTitle";
import { useOwnerOrAdmin } from "./useMe";
import { useRemoveFromCollection, useSubmitTag } from "./api";
import { AddToCollectionDialog } from "./AddToCollection";
import { Identifiers } from "@/features/reader/Identifiers";
import { useLabelById } from "@/features/labels/api";

export interface ResultCardProps {
  hit: SearchHit;
  terms: string[];
  onOpen: (docId: string, opts?: { page?: number; chunkId?: string; bbox?: Bbox | null }) => void;
  onTag: (tag: string) => void;
  /** set when the library is filtered to exactly one collection */
  collectionId?: string;
}

export function ResultCard({ hit, terms, onOpen, onTag, collectionId }: ResultCardProps) {
  const removeFrom = useRemoveFromCollection();
  const canRemove = useOwnerOrAdmin(hit.owner_id);
  const [adding, setAdding] = useState(false);
  const title = docTitle(hit);
  const TypeIcon = hit.file_type === "PDF" ? FileText : FileType2;
  const isDefaultLabel = useLabelById(hit.label?.id)?.is_default;
  return (
    <li className="result" tabIndex={0} data-doc={hit.doc_id}>
      <div style={{ minWidth: 0 }}>
        <div className="rtop">
          <div style={{ minWidth: 0 }}>
            <h3><button title={title} onClick={() => onOpen(hit.doc_id)}>{hit.title.trim() ? highlight(hit.title, terms) : title}</button></h3>
            <CiteLine authors={hit.authors} venue={hit.venue} year={hit.year} />
          </div>
          {hit.flag && <span className="flag"><Clock aria-hidden />{hit.flag}</span>}
          <RowMenu label={`Actions for ${title}`} actions={[
            { label: "Open", onSelect: () => onOpen(hit.doc_id) },
            { label: "Add to collection", onSelect: () => setAdding(true) },
            ...(collectionId ? [{ label: "Remove from collection", danger: true, onSelect: () => removeFrom.mutate({ cid: collectionId, docId: hit.doc_id }) }] : []),
            copyAction("Copy title", title),
          ]} />
        </div>
        <Synopsis summary={hit.summary} abstract={hit.abstract} />
        <Identifiers ids={hit.ids} />
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
          {hit.label && !isDefaultLabel && <SensitivityBadge label={hit.label} />}
          <Badge kind="type"><TypeIcon aria-hidden />{hit.file_type}</Badge>
          <Badge><History aria-hidden />v{hit.version}</Badge>
          {hit.page_count != null && <Badge><BookOpen aria-hidden />{hit.page_count} pages</Badge>}
          {!!hit.ref_count && <Badge><ListTree aria-hidden />{hit.ref_count} references</Badge>}
          <Badge title={new Date(hit.added_at).toLocaleString()}><CalendarPlus aria-hidden />Added {new Date(hit.added_at).toLocaleDateString()}</Badge>
          {!!hit.cited_by_count && <Badge kind="cite"><Quote aria-hidden />Cited by {hit.cited_by_count} here</Badge>}
          <OverflowList
            items={hit.tags} max={4} menuLabel="More tags" getLabel={(t) => t} className="ovl-tags"
            renderItem={(t) => <TagChip key={t} docId={hit.doc_id} tag={t} auto={hit.auto_tags?.includes(t)} canRemove={canRemove} onFilter={() => onTag(t)} />}
          />
          <AddTag docId={hit.doc_id} />
        </div>
      </div>
      {adding && <AddToCollectionDialog docId={hit.doc_id} open onOpenChange={setAdding} />}
    </li>
  );
}

/** Inline "+ tag": a small button that opens an input; Enter adds (same normalisation as the reader TagBar). */
function AddTag({ docId }: { docId: string }) {
  const submit = useSubmitTag(docId);
  const [v, setV] = useState<string | null>(null);
  if (v === null) return <button className="badge-o add-tag" aria-label="Add tag" onClick={() => setV("")}>+ tag</button>;
  return (
    <input
      className="add-tag" autoFocus placeholder="tag" aria-label="New tag" value={v} onChange={(e) => setV(e.target.value)} onBlur={() => setV(null)}
      onKeyDown={(e) => {
        if (e.key === "Escape") setV(null);
        if (e.key === "Enter" && submit(v)) setV(null); // invalid: stays open for editing
      }}
    />
  );
}

/** AI summary, clamped to 3 lines; "More" expands it and shows the extracted abstract under it. */
function Synopsis({ summary, abstract }: { summary?: string | null; abstract?: string | null }) {
  const [open, setOpen] = useState(false);
  if (!summary && !abstract) return null;
  return (
    <div className={open ? "synopsis open" : "synopsis"}>
      {summary && <p className="syn-sum"><Sparkles aria-hidden /><span className="sr-only">AI summary: </span>{summary}</p>}
      {open && abstract && <p className="syn-abs"><strong>Abstract.</strong> {abstract}</p>}
      <button type="button" className="btn-link" aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? "Less" : abstract ? "More and abstract" : "More"}
      </button>
    </div>
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
