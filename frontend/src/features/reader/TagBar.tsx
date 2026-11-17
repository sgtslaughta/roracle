import { Tag } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { OverflowList } from "@/components/OverflowList";
import { useAddTag } from "@/features/library/api";
import { TagChip } from "@/features/library/TagChip";
import { useOwnerOrAdmin } from "@/features/library/useMe";
import { useLibraryNav } from "@/features/library/useLibraryNav";

export function TagBar({ docId, tags, ownerId }: { docId: string; tags: string[]; ownerId?: string }) {
  const canRemove = useOwnerOrAdmin(ownerId);
  const add = useAddTag(docId);
  const nav = useLibraryNav();
  const [v, setV] = useState("");
  return (
    <div className="tagbar">
      <Tag aria-hidden />
      <OverflowList
        items={tags} max={6} menuLabel="More tags" getLabel={(t) => t}
        renderItem={(t) => <TagChip key={t} docId={docId} tag={t} plain canRemove={canRemove} onFilter={() => nav.patch({ tags: [...new Set([...(nav.search.tags ?? []), t])], tags_not: undefined })} />}
      />
      <input
        placeholder="Add a tag" aria-label="Add tag" value={v} onChange={(e) => setV(e.target.value)}
        onKeyDown={(e) => {
          if (e.key !== "Enter" || !v.trim()) return;
          add.mutate(v.trim(), { onSuccess: () => toast(`Tagged #${v.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-")}`) });
          setV("");
        }}
      />
    </div>
  );
}
