import { EyeOff } from "lucide-react";
import type { ExcludedByLabel } from "@/api/chat";
import { OverflowList } from "@/components/OverflowList";
import { useDoc } from "@/features/reader/api";
import { docTitle } from "@/lib/docTitle";

function Item({ x }: { x: ExcludedByLabel }) {
  const d = useDoc(x.doc_id).data;
  const t = d ? docTitle(d) : "A document";
  return <span className="badge-o" title={`${t} (${x.label})`}><span className="t">{t}</span> · {x.label}</span>;
}

/** Context you pinned or opened that was left out of this answer because its label excludes AI use. */
export function ExcludedNote({ items }: { items: ExcludedByLabel[] }) {
  return (
    <div className="ans-note" role="note">
      <EyeOff aria-hidden />
      <span>Not used for this answer, because their label keeps them away from AI:</span>
      <OverflowList items={items} max={3} menuLabel="More excluded documents" getLabel={(x) => x.label} renderItem={(x) => <Item key={x.doc_id} x={x} />} />
    </div>
  );
}
