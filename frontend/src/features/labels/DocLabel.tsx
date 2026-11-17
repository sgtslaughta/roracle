import { errorText } from "@/api/client";
import type { DocumentDetail } from "@/api/types";
import { docTitle } from "@/lib/docTitle";
import { useSetDocLabel } from "./api";
import { LabelControl } from "./LabelControl";

/** The document's label badge; owner/admin also get the picker. */
export function DocLabel({ doc, canManage }: { doc: DocumentDetail; canManage: boolean }) {
  const set = useSetDocLabel(doc.id);
  return (
    <LabelControl
      title={docTitle(doc)} current={doc.label ?? null} pending={set.isPending} error={set.error ? errorText(set.error) : null} reset={set.reset}
      onApply={canManage ? (label_id, reason, done) => set.mutate({ label_id, reason }, { onSuccess: done }) : undefined}
    />
  );
}
