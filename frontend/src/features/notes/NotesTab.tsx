import { Plus } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { NOTE_KINDS, type Note, type NoteKind } from "@/api/notes";
import { Button } from "@/components/ui/button";
import { useMe } from "@/features/library/useMe";
import { openDoc } from "@/features/reader/useReaderStore";
import { notesUi, useNotes } from "./api";
import { EvidencePanel } from "./Evidence";
import { KIND_LABEL } from "./labels";
import { NoteCard } from "./NoteCard";
import { NoteComposer } from "./NoteComposer";

export const NOTES_PAGE = 20;

/** Top-level notes (oldest first, as the API returns them) with their replies; replies whose thread isn't readable are dropped. */
export function threads(notes: Note[]): { note: Note; replies: Note[] }[] {
  const byParent = new Map<string, Note[]>();
  for (const n of notes) if (n.parent_id) byParent.set(n.parent_id, [...(byParent.get(n.parent_id) ?? []), n]);
  return notes.filter((n) => !n.parent_id).map((note) => ({ note, replies: byParent.get(note.id) ?? [] }));
}

export function NotesTab({ docId, docOwnerId, version, latest }: { docId: string; docOwnerId: string; version: number; latest: number }) {
  const { data, isLoading, isError } = useNotes(docId);
  const { data: me } = useMe();
  const draft = notesUi.use((s) => (s.draft?.docId === docId ? s.draft : null));
  const flash = notesUi.use((s) => s.flash);
  const [composing, setComposing] = useState(false);
  const [kind, setKind] = useState<NoteKind | "">("");
  const [limit, setLimit] = useState(NOTES_PAGE);

  const all = useMemo(() => threads(data ?? []), [data]);
  const list = kind ? all.filter((t) => t.note.kind === kind) : all;
  const flashRoot = flash ? all.find((t) => t.note.id === flash || t.replies.some((r) => r.id === flash)) : undefined;

  // a marker click: make the card visible, scroll to it, flash it briefly
  useEffect(() => {
    if (!flash) return;
    if (!flashRoot) { if (data) notesUi.set((s) => ({ ...s, flash: null })); return; } // not (or no longer) readable
    if (kind && flashRoot.note.kind !== kind) setKind("");
    const i = all.indexOf(flashRoot);
    if (i >= limit) setLimit(i + 1);
    const raf = requestAnimationFrame(() => {
      const root = document.getElementById(`note-${flashRoot.note.id}`);
      document.getElementById(`note-${flash}`)?.scrollIntoView({ block: "center" });
      root?.focus({ preventScroll: true }); // keyboard users land on the card the marker pointed to
    });
    const t = setTimeout(() => notesUi.set((s) => ({ ...s, flash: null })), 2000);
    return () => { cancelAnimationFrame(raf); clearTimeout(t); };
  }, [flash, flashRoot, data]); // eslint-disable-line react-hooks/exhaustive-deps

  const open = composing || !!draft;
  const close = () => { setComposing(false); notesUi.set((s) => ({ ...s, draft: null })); };

  return (
    <div className="refs notes">
      <p className="refnote">Notes from readers and their agents. Agent notes appear only after their user approves them.</p>
      <div className="row2 ntools">
        <select className="sel" aria-label="Filter by kind" value={kind} onChange={(e) => { setKind(e.target.value as NoteKind | ""); setLimit(NOTES_PAGE); }}>
          <option value="">All kinds</option>
          {NOTE_KINDS.map((k) => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}
        </select>
        {!open && <Button onClick={() => setComposing(true)}><Plus aria-hidden />New note</Button>}
      </div>
      <div className="ntray">
        {open && <NoteComposer key={draft ? `${draft.page}:${draft.quote}` : "new"} docId={docId} draft={draft} latest={latest} onDone={close} />}
        {isLoading && <p className="empty" role="status">Loading notes…</p>}
        {isError && <p className="empty" role="alert">Notes couldn't be loaded. Try again later.</p>}
        {data && !list.length && <p className="empty">{kind ? `No ${KIND_LABEL[kind].toLowerCase()} notes.` : "No notes yet. Choose Add note and click a passage, or write a note for the whole document."}</p>}
        {list.slice(0, limit).map(({ note, replies }) => (
          <NoteCard
            key={note.id} note={note} replies={replies} docId={docId} me={me} docOwnerId={docOwnerId} viewedVersion={version}
            flash={!!flash && (flash === note.id || replies.some((r) => r.id === flash))}
            expand={flashRoot?.note.id === note.id && flash !== note.id}
            onGoTo={(page) => openDoc(docId, { page, version })}
            evidence={note.kind === "finding" ? <EvidencePanel note={note} docId={docId} me={me} /> : undefined}
          />
        ))}
        {list.length > limit && <Button onClick={() => setLimit(limit + NOTES_PAGE)}>Show more ({list.length - limit} left)</Button>}
      </div>
    </div>
  );
}
