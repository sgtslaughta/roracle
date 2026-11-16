import { fmtDate } from "@/lib/dates";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { useMe } from "@/features/library/useMe";
import { useNeedsTitle } from "./api";
import { TitleForm } from "./TitlePrompt";
import { copyAction, RowMenu } from "@/components/RowMenu";

const SHOWN = 10;

/** Admin-only: every upload waiting for a title. Capped at 10 rows until expanded. */
export function NeedsTitleQueue() {
  const { data: me } = useMe();
  const { data } = useNeedsTitle(!!me?.is_admin);
  const [all, setAll] = useState(false);
  if (!me?.is_admin || !data) return null;
  const rows = all ? data : data.slice(0, SHOWN);
  return (
    <section className="ntq" aria-labelledby="ntq-h">
      <h2 id="ntq-h">Waiting for a title</h2>
      <p className="sub">Uploads from anyone that need a title before they can be searched. Oldest first.</p>
      {!data.length && <p className="sub">Nothing is waiting for a title.</p>}
      <ul>
        {rows.map((r) => (
          <li key={r.version_id}>
            <div className="who">
              <b title={r.filename}>{r.filename}</b>
              <small>{r.owner}. {fmtDate(r.uploaded_at)}</small>
            </div>
            <RowMenu label={`Actions for ${r.filename}`} actions={[copyAction("Copy filename", r.filename), copyAction("Copy owner", r.owner)]} />
            <TitleForm documentId={r.document_id} versionId={r.version_id} label={`Title for ${r.filename}`} />
          </li>
        ))}
      </ul>
      {!all && data.length > SHOWN && <Button onClick={() => setAll(true)}>Show all {data.length}</Button>}
    </section>
  );
}
