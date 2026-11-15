import { Tag } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { useAddTag } from "@/features/library/api";
import { useLibraryNav } from "@/features/library/useLibraryNav";

export function TagBar({ docId, tags }: { docId: string; tags: string[] }) {
  const add = useAddTag(docId);
  const nav = useLibraryNav();
  const [v, setV] = useState("");
  return (
    <div className="tagbar">
      <Tag aria-hidden />
      {tags.map((t) => <button key={t} className="badge-o" onClick={() => nav.add("tags", t)} title="Filter the library by this tag">#{t}</button>)}
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
