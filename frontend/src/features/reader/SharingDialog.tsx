import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { apiFetch, seg } from "@/api/client";
import type { DocumentDetail, DocumentPatch, Visibility } from "@/api/types";
import { ChipInput } from "@/features/ask/ChipInput";
import { VIS_LABEL } from "@/features/uploads/status";
import { VIS_ORDER } from "@/features/uploads/UploadOptions";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const userError = (v: string) => (UUID.test(v) ? null : "Enter the person's user id (a UUID).");
const groupError = (v: string) => (v.startsWith("/") ? null : "Group paths start with “/”, for example /Mathematics/Spectral Theory.");

/** Owner/admin: who can see this document. Shares only count while visibility is "shared". */
export function SharingDialog({ doc, open, onOpenChange }: { doc: DocumentDetail; open: boolean; onOpenChange: (o: boolean) => void }) {
  const qc = useQueryClient();
  const [vis, setVis] = useState<Visibility>(doc.visibility);
  const [users, setUsers] = useState<string[]>([]);
  const [groups, setGroups] = useState<string[]>([]);
  const save = useMutation({
    mutationFn: (body: DocumentPatch) => apiFetch(`/documents/${seg(doc.id)}`, { method: "PATCH", json: body }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["doc", doc.id] });
      void qc.invalidateQueries({ queryKey: ["search"] });
      toast("Sharing updated.");
      onOpenChange(false);
    },
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogTitle>Sharing</DialogTitle>
        <DialogDescription>Choose who can see “{doc.title}”.</DialogDescription>
        <form style={{ display: "grid", gap: 10 }} onSubmit={(e) => { e.preventDefault(); save.mutate({ visibility: vis, ...(vis === "shared" ? { share_users: users, share_groups: groups } : {}) }); }}>
          <label>Visible to
            <select className="inp" value={vis} onChange={(e) => setVis(e.target.value as Visibility)}>
              {VIS_ORDER.map((v) => <option key={v} value={v}>{VIS_LABEL[v]}</option>)}
            </select>
          </label>
          <p className="sub">Shares only apply when visibility is “Shared”. Switching to “Only me” clears all shares.</p>
          {vis === "shared" && (
            <>
              <ChipInput label="Share with users" help="User ids; replaces the current list." values={users} onChange={setUsers} validate={userError} />
              <ChipInput label="Share with groups" help="Group paths; replaces the current list." values={groups} onChange={setGroups} validate={groupError} />
            </>
          )}
          <Button variant="primary" type="submit" disabled={save.isPending}>Save</Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}
