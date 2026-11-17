import { useState } from "react";
import { toast } from "sonner";
import { errorText } from "@/api/client";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { useDeleteCollection, useRenameCollection } from "@/features/library/api";

export interface CollectionEdit { id: string; name: string; mode: "rename" | "delete" }

/** Rename / delete confirmation for the collection picked in the sidebar menu. */
export function CollectionDialogs({ edit, onClose }: { edit: CollectionEdit | null; onClose: () => void }) {
  const rename = useRenameCollection(), del = useDeleteCollection();
  if (!edit) return null;
  if (edit.mode === "delete") {
    return (
      <ConfirmDialog open onOpenChange={(o) => !o && onClose()} title={`Delete “${edit.name}”?`} confirm="Delete collection" busy={del.isPending}
        onConfirm={() => del.mutate(edit.id, { onSuccess: () => { toast("Collection deleted. Its documents are untouched."); onClose(); }, onError: (e) => toast(errorText(e)) })}>
        The collection is removed for you. The documents in it stay in the library.
      </ConfirmDialog>
    );
  }
  return <RenameDialog key={edit.id} edit={edit} onClose={onClose} pending={rename.isPending} run={(name) => rename.mutate({ id: edit.id, name }, { onSuccess: () => { toast("Collection renamed."); onClose(); }, onError: (e) => toast(errorText(e)) })} />;
}

function RenameDialog({ edit, onClose, run, pending }: { edit: CollectionEdit; onClose: () => void; run: (name: string) => void; pending: boolean }) {
  const [name, setName] = useState(edit.name);
  const ok = !!name.trim() && name.trim() !== edit.name;
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="w-[min(440px,calc(100vw-32px))]">
        <DialogTitle>Rename collection</DialogTitle>
        <DialogDescription>Only you see your collections.</DialogDescription>
        <form className="lpick" onSubmit={(e) => { e.preventDefault(); if (ok) run(name.trim()); }}>
          <label>Name<input className="inp" value={name} maxLength={100} autoFocus onChange={(e) => setName(e.target.value)} /></label>
          <div className="foot">
            <Button variant="ghost" type="button" onClick={onClose}>Cancel</Button>
            <Button variant="primary" type="submit" disabled={!ok || pending}>Rename</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
