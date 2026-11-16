import { Link } from "@tanstack/react-router";
import { GitCompare } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import type { ReviewItem, ReviewSource } from "@/api/admin";
import type { NearDupAction } from "@/api/types";
import { Button } from "@/components/ui/button";
import { isDocId } from "@/lib/ids";
import { useNearDup } from "@/features/uploads/api";
import { REVIEW_PAGE, useReview } from "./api";
import { fmtTime } from "./ConnectorsPage";
import { copyAction, RowMenu } from "@/components/RowMenu";

const SOURCES: [ReviewSource, string][] = [["all", "All"], ["upload", "Uploads"], ["connector", "Connectors"]];

/** Near-duplicate queue: the same decision as the uploader's prompt, made on their behalf. */
export function ReviewPage() {
  const [source, setSource] = useState<ReviewSource>("all");
  const [page, setPage] = useState(1);
  const { data, isError, isFetching } = useReview(source, page);
  const nearDup = useNearDup();
  const decide = (r: ReviewItem, action: NearDupAction) =>
    nearDup.mutate({ documentId: r.document_id, versionId: r.version_id, action }, {
      onSuccess: () => toast(action === "new_version" ? `${r.filename} added as a new version.` : action === "separate" ? `${r.filename} kept as its own document.` : `${r.filename} cancelled.`),
      onError: (e) => toast(e instanceof ApiError ? e.message : "The decision couldn't be saved. Try again."),
    });
  const pages = data ? Math.max(1, Math.ceil(data.total / REVIEW_PAGE)) : 1;
  useEffect(() => { if (data && page > pages) setPage(pages); }, [data, page, pages]);

  return (
    <div className="admin-page">
      <div className="uhead-row">
        <p className="sub">Uploads that look like documents already in the library. Oldest first.</p>
        <div className="seg" role="group" aria-label="Source">
          {SOURCES.map(([k, l]) => <button key={k} aria-pressed={source === k} onClick={() => { setSource(k); setPage(1); }}>{l}</button>)}
        </div>
      </div>
      {isError && <p role="alert">The queue couldn't be loaded. Reload to try again.</p>}
      {data && !data.items.length && <div className="empty"><GitCompare aria-hidden /><p>Nothing to review.</p></div>}
      {data && data.items.length > 0 && (
        <div className="tok-wrap" aria-busy={isFetching}>
          <table className="agent-table admin-table">
            <thead><tr><th>File</th><th>Owner</th><th>From</th><th>Uploaded</th><th>Looks like</th><th><span className="sr-only">Decide</span></th></tr></thead>
            <tbody>
              {data.items.map((r) => (
                <tr key={r.version_id}>
                  <td className="trunc" title={r.filename}>{r.filename}</td>
                  <td>{r.owner}</td>
                  <td>{r.connector_id ? "Connector" : "Upload"}</td>
                  <td>{fmtTime(r.uploaded_at)}</td>
                  <td className="wrap">
                    {r.match ? (isDocId(r.match.document_id) ? <Link to="/d/$docId" params={{ docId: r.match.document_id }}>{r.match.title}</Link> : r.match.title) : "A document in the library"}
                  </td>
                  <td>
                    <RowMenu kebab={false} label={`Actions for ${r.filename}`} actions={[
                      { label: "Add as new version", onSelect: () => decide(r, "new_version") },
                      { label: "Keep separate", onSelect: () => decide(r, "separate") },
                      { label: "Cancel upload", danger: true, onSelect: () => decide(r, "cancel") },
                      copyAction("Copy filename", r.filename),
                    ]} />
                    <span className="acts">
                      <Button variant="primary" disabled={nearDup.isPending} aria-label={`Add ${r.filename} as new version`} onClick={() => decide(r, "new_version")}>New version</Button>
                      <Button disabled={nearDup.isPending} aria-label={`Keep ${r.filename} separate`} onClick={() => decide(r, "separate")}>Keep separate</Button>
                      <Button variant="ghost" disabled={nearDup.isPending} aria-label={`Cancel upload of ${r.filename}`} onClick={() => decide(r, "cancel")}>Cancel upload</Button>
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {data && data.total > REVIEW_PAGE && (
        <div className="pager">
          <Button disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Previous</Button>
          <span>Page {page} of {pages}</span>
          <Button disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>Next</Button>
        </div>
      )}
    </div>
  );
}
