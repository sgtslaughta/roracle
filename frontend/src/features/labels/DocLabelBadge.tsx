import { SensitivityBadge } from "@/components/SensitivityBadge";
import { useDoc } from "@/features/reader/api";

/** A document's label by id (shares the reader's document cache). Renders nothing until known. */
export function DocLabelBadge({ docId, className }: { docId: string; className?: string }) {
  const label = useDoc(docId).data?.label;
  return label ? <SensitivityBadge label={label} className={className} /> : null;
}
