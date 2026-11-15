import { Columns2, GitCompare } from "lucide-react";
import type { NearDupAction, UploadItem } from "@/api/types";
import { Button } from "@/components/ui/button";
import { openDoc } from "@/features/reader/useReaderStore";
import { useNearDup } from "./api";

export function NearDupPrompt({ u }: { u: UploadItem }) {
  const m = useNearDup();
  const act = (action: NearDupAction) => m.mutate({ documentId: u.document_id, versionId: u.version_id, action });
  const d = u.near_dup_of;
  return (
    <div className="tdet review">
      <span className="msg">
        <GitCompare aria-hidden />
        <span>Very close to <b>{d?.title ?? "a document in the library"}</b>. Is it the same document?</span>
      </span>
      <span className="acts">
        <Button variant="primary" disabled={m.isPending} onClick={() => act("new_version")}>Yes, add as new version</Button>
        <Button disabled={m.isPending} onClick={() => act("separate")}>No, keep separate</Button>
        {d && <Button variant="ghost" onClick={() => openDoc(d.document_id)}><Columns2 aria-hidden />Compare</Button>}
        <Button variant="ghost" disabled={m.isPending} onClick={() => act("cancel")}>Cancel upload</Button>
      </span>
    </div>
  );
}
