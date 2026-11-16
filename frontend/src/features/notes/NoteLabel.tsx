import { errorText } from "@/api/client";
import type { Note } from "@/api/notes";
import type { Me } from "@/api/types";
import { useSetNoteLabel } from "@/features/labels/api";
import { LabelControl } from "@/features/labels/LabelControl";
import { useDoc } from "@/features/reader/api";

/** "Change label" for a top-level note: its author or an admin. Raising is free; lowering is admin-only (the picker explains). */
export function NoteLabel({ note, docId, me }: { note: Note; docId: string; me: Me | undefined }) {
  const set = useSetNoteLabel(docId);
  const doc = useDoc(docId).data;
  if (!me || !(me.is_admin || me.id === note.author.id) || !doc?.label) return null;
  const effective = note.effective_label ?? note.label ?? doc.label;
  return (
    <LabelControl
      hideBadge title="this note" current={effective} floor={doc.label} pending={set.isPending} error={set.error ? errorText(set.error) : null} reset={set.reset}
      onApply={(label_id, reason, done) => set.mutate({ id: note.id, label_id, reason }, { onSuccess: done })}
    />
  );
}
