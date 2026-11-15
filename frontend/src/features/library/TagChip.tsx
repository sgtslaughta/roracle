import { toast } from "sonner";
import { ChipMenu, type ChipAction } from "@/components/ChipMenu";
import { useRemoveTag } from "./api";

/** A tag chip: click filters the library; right-click offers filter, copy and (owner/admin) remove. */
export function TagChip({ docId, tag, canRemove, onFilter, plain }: { docId: string; tag: string; canRemove: boolean; onFilter: () => void; plain?: boolean }) {
  const remove = useRemoveTag(docId);
  const actions: ChipAction[] = [
    { label: "Filter by this tag", onSelect: onFilter },
    { label: "Copy tag", onSelect: () => { void navigator.clipboard?.writeText(tag); toast(`Copied #${tag}`); } },
    ...(canRemove ? [{ label: "Remove tag", onSelect: () => remove.mutate(tag, { onSuccess: () => toast(`Removed #${tag}`) }) }] : []),
  ];
  return (
    <ChipMenu label={`Actions for tag ${tag}`} actions={actions}>
      <button className={plain ? "badge-o" : "badge-o tag"} title={`#${tag}`} onClick={onFilter}><span className="t">#{tag}</span></button>
    </ChipMenu>
  );
}
