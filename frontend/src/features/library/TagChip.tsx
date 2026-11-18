import { toast } from "sonner";
import { ChipMenu, type ChipAction } from "@/components/ChipMenu";
import { useRemoveTag } from "./api";

export const AUTO_TAG_HINT = "Suggested by AI from the summary";

/** A tag chip: click filters the library; right-click offers filter, copy and (owner/admin) remove. `auto`: suggested by the LLM. */
export function TagChip({ docId, tag, canRemove, onFilter, plain, auto }: { docId: string; tag: string; canRemove: boolean; onFilter: () => void; plain?: boolean; auto?: boolean }) {
  const remove = useRemoveTag(docId);
  const actions: ChipAction[] = [
    { label: "Filter by this tag", onSelect: onFilter },
    { label: "Copy tag", onSelect: () => { void navigator.clipboard?.writeText(tag); toast(`Copied #${tag}`); } },
    ...(canRemove ? [{ label: "Remove tag", onSelect: () => remove.mutate(tag, { onSuccess: () => toast(`Removed #${tag}`) }) }] : []),
  ];
  return (
    <ChipMenu label={`Actions for tag ${tag}`} actions={actions}>
      <button className={`${plain ? "badge-o" : "badge-o tag"}${auto ? " auto" : ""}`} title={auto ? `#${tag} · ${AUTO_TAG_HINT}` : `#${tag}`} onClick={onFilter}>
        <span className="t">#{tag}</span>{auto && <span className="sr-only"> ({AUTO_TAG_HINT})</span>}
      </button>
    </ChipMenu>
  );
}
