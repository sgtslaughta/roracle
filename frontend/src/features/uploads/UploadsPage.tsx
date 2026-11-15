import { useState } from "react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import { cn } from "@/lib/utils";
import { isDuplicate, useUploads, useUpload } from "./api";
import { DropZone } from "./DropZone";
import { DuplicateDialog } from "./DuplicateDialog";
import { FILTERS, MAX_UPLOAD_BYTES, viewStatus, type ViewStatus } from "./status";
import { UploadRow } from "./UploadRow";

export function UploadsPage() {
  const { data } = useUploads();
  const upload = useUpload();
  const [filter, setFilter] = useState<ViewStatus | "all">("all");
  const [dups, setDups] = useState<File[]>([]);
  const items = data?.items ?? [];
  const count = (k: ViewStatus | "all") => (k === "all" ? items.length : items.filter((u) => viewStatus(u) === k).length);
  const rows = items.filter((u) => filter === "all" || viewStatus(u) === filter);

  const send = async (file: File, force = false) => {
    try {
      await upload.mutateAsync({ file, force });
      toast(`${file.name} uploaded. Processing has started.`);
    } catch (e) {
      if (isDuplicate(e)) setDups((d) => [...d, file]);
      else toast(e instanceof ApiError ? `${file.name}: ${e.message}` : `${file.name} couldn't be uploaded. Try again.`);
    }
  };
  const onFiles = (files: File[]) => {
    setFilter("all");
    for (const f of files) {
      if (f.size > MAX_UPLOAD_BYTES) toast(`${f.name} is over the 500 MB limit. Split it, or ask an admin to raise the limit.`);
      else void send(f);
    }
  };

  return (
    <section className="view">
      <div className="uhead-row">
        <div>
          <h1>My uploads</h1>
          <p className="sub">Each file's progress from upload until it's searchable.</p>
        </div>
        <div className="seg" role="group" aria-label="Filter uploads">
          {FILTERS.map(([k, l]) => (
            <button key={k} aria-pressed={filter === k} onClick={() => setFilter(k)}>{l}<span className="n">{count(k)}</span></button>
          ))}
        </div>
      </div>
      <DropZone onFiles={onFiles} />
      <div className={cn("tbl")}>
        <div className="trow h"><span>File</span><span>Progress</span><span>Status</span><span>Visible to</span></div>
        {rows.map((u) => <UploadRow key={u.version_id} u={u} />)}
        {!rows.length && <div className="trow"><span className="vis">No uploads in this view.</span></div>}
      </div>
      <DuplicateDialog
        file={dups[0] ?? null}
        onCancel={() => setDups((d) => d.slice(1))}
        onAdd={() => { const f = dups[0]; setDups((d) => d.slice(1)); void send(f, true); }}
      />
    </section>
  );
}
