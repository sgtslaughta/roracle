import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch, seg, QUIET } from "@/api/client";
import type { Note, NoteIn, NotePatch, NoteVotes, ReportReason } from "@/api/notes";
import type { Bbox } from "@/api/types";
import { createStore } from "@/lib/store";
import { openDoc, setView } from "@/features/reader/useReaderStore";

const key = (docId: string) => ["notes", docId];

export const useNotes = (docId: string) =>
  useQuery({ queryKey: key(docId), queryFn: async () => (await apiFetch<{ items: Note[] }>(`/documents/${seg(docId)}/annotations`)).items });

/** Every note write refreshes the document's list and search results (note hits) (re-anchoring, reply visibility and votes all come from the server). */
function useNoteMutation<V, R>(docId: string, fn: (v: V) => Promise<R>) {
  const qc = useQueryClient();
  return useMutation({ meta: QUIET, mutationFn: fn, onSettled: () => Promise.all([qc.invalidateQueries({ queryKey: key(docId) }), qc.invalidateQueries({ queryKey: ["search"] })]) });
}

export const useCreateNote = (docId: string) =>
  useNoteMutation(docId, (body: NoteIn) => apiFetch<Note>(`/documents/${seg(docId)}/annotations`, { method: "POST", json: body }));
export const useReply = (docId: string) =>
  useNoteMutation(docId, ({ id, body }: { id: string; body: string }) => apiFetch<Note>(`/annotations/${seg(id)}/replies`, { method: "POST", json: { body } }));
export const usePatchNote = (docId: string) =>
  useNoteMutation(docId, ({ id, patch }: { id: string; patch: NotePatch }) => apiFetch<Note>(`/annotations/${seg(id)}`, { method: "PATCH", json: patch }));
export const useDeleteNote = (docId: string) =>
  useNoteMutation(docId, (id: string) => apiFetch<void>(`/annotations/${seg(id)}`, { method: "DELETE" }));
export const useHideNote = (docId: string) =>
  useNoteMutation(docId, ({ id, hidden }: { id: string; hidden: boolean }) => apiFetch<Note>(`/annotations/${seg(id)}/hide`, { method: "POST", json: { hidden } }));
export const useRemoveNote = (docId: string) =>
  useNoteMutation(docId, (id: string) => apiFetch<void>(`/admin/annotations/${seg(id)}`, { method: "DELETE" }));
export const reportNote = (id: string, reason: ReportReason) =>
  apiFetch<void>(`/annotations/${seg(id)}/report`, { method: "POST", json: { reason } });

/** PUT vote: the response carries the new tallies, patched straight into the list. */
export function useVoteNote(docId: string) {
  const qc = useQueryClient();
  return useMutation({
    meta: QUIET,
    mutationFn: ({ id, value }: { id: string; value: 1 | -1 | 0 }) => apiFetch<NoteVotes>(`/annotations/${seg(id)}/vote`, { method: "PUT", json: { value } }),
    onSuccess: (votes, { id }) => qc.setQueryData<Note[]>(key(docId), (old) => old?.map((n) => (n.id === id ? { ...n, votes } : n))),
  });
}

/** Reader <-> notes hand-off: "Add note" mode, the picked passage, the page in view, and the card to flash. */
/** `version`: the version the passage was picked on; new notes attach to the latest version, so only then is `bbox` kept. */
export interface NoteDraft { docId: string; version: number; quote: string; page: number; bbox: Bbox | null }
interface NotesUi { adding: string | null; draft: NoteDraft | null; page: number; flash: string | null }
export const notesUi = createStore<NotesUi>({ adding: null, draft: null, page: 1, flash: null });

/** Open a note in the reader's Notes tab and flash it. */
export function showNote(docId: string, noteId: string) {
  openDoc(docId);
  setView("notes");
  notesUi.set((s) => ({ ...s, flash: noteId }));
}
