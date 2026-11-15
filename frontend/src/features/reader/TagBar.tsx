import { Tag } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { ChipMenu } from "@/components/ChipMenu";
import { OverflowList } from "@/components/OverflowList";
import { useAddTag } from "@/features/library/api";
import { useLibraryNav } from "@/features/library/useLibraryNav";

export function TagBar({ docId, tags }: { docId: string; tags: string[] }) {
  const add = useAddTag(docId);
  const nav = useLibraryNav();
  const [v, setV] = useState("");
  return (
    <div className="tagbar">
      <Tag aria-hidden />
      <OverflowList
        items={tags} max={6} menuLabel="More tags" getLabel={(t) => t}
        renderItem={(t) => (
          <ChipMenu key={t} label={`Actions for tag ${t}`} actions={[
            { label: "Filter library by this tag", onSelect: () => nav.add("tags", t) },
            { label: "Copy tag", onSelect: () => { void navigator.clipboard?.writeText(t); toast(`Copied #${t}`); } },
          ]}>
            <button className="badge-o" title={`#${t} (click to filter the library)`} onClick={() => nav.add("tags", t)}><span className="t">#{t}</span></button>
          </ChipMenu>
        )}
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
