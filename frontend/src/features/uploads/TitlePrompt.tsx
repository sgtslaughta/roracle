import { Pencil } from "lucide-react";
import { useState, type FormEvent } from "react";
import type { UploadItem } from "@/api/types";
import { Button } from "@/components/ui/button";
import { useRename, useSetTitle } from "./api";

/** Title input + "Save title": Enter submits; blank (after trim) is refused client-side. */
export function TitleForm({ documentId, versionId, label = "Document title" }: { documentId: string; versionId: string; label?: string }) {
  const m = useSetTitle();
  const [v, setV] = useState("");
  const title = v.trim();
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (title) m.mutate({ documentId, versionId, title });
  };
  return (
    <form className="titleform" onSubmit={submit}>
      <input className="inp" aria-label={label} maxLength={500} required value={v} onChange={(e) => setV(e.target.value)} />
      <Button type="submit" variant="primary" disabled={m.isPending || !title}>Save title</Button>
    </form>
  );
}

export function TitlePrompt({ u }: { u: UploadItem }) {
  return (
    <div className="tdet title">
      <span className="msg"><Pencil aria-hidden />We couldn't find a title in this file. Add one so people can find it.</span>
      <TitleForm documentId={u.document_id} versionId={u.version_id} />
    </div>
  );
}

/** A finished upload that still has no title: offer a one-line "Add title" (PATCH, not the processing-stage PUT). */
export function AddTitle({ u }: { u: UploadItem }) {
  const m = useRename();
  const [open, setOpen] = useState(false);
  const [v, setV] = useState("");
  if (!open) return <Button variant="ghost" onClick={() => setOpen(true)}>Add title</Button>;
  const title = v.trim();
  return (
    <form className="titleform" onSubmit={(e) => { e.preventDefault(); if (title) m.mutate({ documentId: u.document_id, title }); }}>
      <input className="inp" aria-label={`Title for ${u.filename}`} maxLength={500} required autoFocus value={v} onChange={(e) => setV(e.target.value)} />
      <Button type="submit" variant="primary" disabled={m.isPending || !title}>Save title</Button>
    </form>
  );
}
