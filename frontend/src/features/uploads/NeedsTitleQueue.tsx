import { useState } from "react";
import { Button } from "@/components/ui/button";
import { useMe } from "@/features/library/useMe";
import { useNeedsTitle } from "./api";
import { TitleForm } from "./TitlePrompt";

const SHOWN = 10;

/** Admin-only: every upload waiting for a title. Capped at 10 rows until expanded. */
export function NeedsTitleQueue() {
  const { data: me } = useMe();
  const { data } = useNeedsTitle(!!me?.is_admin);
  const [all, setAll] = useState(false);
  if (!me?.is_admin || !data?.length) return null;
  const rows = all ? data : data.slice(0, SHOWN);
  return (
    <section className="ntq" aria-label="Waiting for a title">
      <h2>Waiting for a title</h2>
      <p className="sub">Uploads from anyone that need a title before they can be searched. Oldest first.</p>
      <ul>
        {rows.map((r) => (
          <li key={r.version_id}>
            <div className="who">
              <b title={r.filename}>{r.filename}</b>
              <small>{r.owner}. {new Date(r.uploaded_at).toLocaleDateString([], { dateStyle: "medium" })}</small>
            </div>
            <TitleForm documentId={r.document_id} versionId={r.version_id} label={`Title for ${r.filename}`} />
          </li>
        ))}
      </ul>
      {!all && data.length > SHOWN && <Button onClick={() => setAll(true)}>Show all {data.length}</Button>}
    </section>
  );
}
