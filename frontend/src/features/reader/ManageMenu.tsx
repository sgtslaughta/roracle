import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { apiFetch, errorText, seg } from "@/api/client";
import type { DocumentDetail, Person } from "@/api/types";
import { DirectoryPicker } from "@/components/DirectoryPicker";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { RowMenu } from "@/components/RowMenu";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { docTitle } from "@/lib/docTitle";
import { closeTab } from "./useReaderStore";

/** Owner/admin: delete (soft, confirmed) and transfer ownership. */
export function ManageMenu({ doc }: { doc: DocumentDetail }) {
  const qc = useQueryClient();
  const [mode, setMode] = useState<"delete" | "transfer" | null>(null);
  const [target, setTarget] = useState<Person | null>(null);
  const title = docTitle(doc);
  const done = () => Promise.all(["search", "uploads", "collections"].map((k) => qc.invalidateQueries({ queryKey: [k] })).concat(qc.invalidateQueries({ queryKey: ["doc", doc.id] })));
  const del = useMutation({
    mutationFn: () => apiFetch<void>(`/documents/${seg(doc.id)}`, { method: "DELETE" }),
    onSuccess: () => { setMode(null); closeTab(doc.id); toast("Document deleted."); return done(); },
    onError: (e) => toast(errorText(e)),
  });
  const move = useMutation({
    mutationFn: (user_id: string) => apiFetch(`/documents/${seg(doc.id)}/transfer`, { method: "POST", json: { user_id } }),
    onSuccess: () => { setMode(null); setTarget(null); toast("Ownership transferred."); return done(); },
    onError: (e) => toast(errorText(e)),
  });
  return (
    <>
      <RowMenu label={`Manage ${title}`} actions={[
        { label: "Transfer ownership…", onSelect: () => setMode("transfer") },
        { label: "Delete document…", danger: true, onSelect: () => setMode("delete") },
      ]} />
      <ConfirmDialog open={mode === "delete"} onOpenChange={(o) => !o && setMode(null)} title="Delete this document?" confirm="Delete" busy={del.isPending} onConfirm={() => del.mutate()}>
        “{title}” disappears from the library for everyone. It is kept for the retention period, then purged.
      </ConfirmDialog>
      <Dialog open={mode === "transfer"} onOpenChange={(o) => { if (!o) { setMode(null); setTarget(null); } }}>
        <DialogContent className="w-[min(440px,calc(100vw-32px))]">
          <DialogTitle>Transfer ownership</DialogTitle>
          <DialogDescription>The new owner controls “{title}”, including sharing, labels and deletion.</DialogDescription>
          <DirectoryPicker<Person> kind="users" label="New owner" exclude={[doc.owner.id]} onPick={setTarget} />
          {target && <p role="status">New owner: <b>{target.username}</b></p>}
          <div className="foot">
            <Button variant="ghost" onClick={() => setMode(null)}>Cancel</Button>
            <Button variant="primary" disabled={!target || move.isPending} onClick={() => target && move.mutate(target.id)}>Transfer</Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
