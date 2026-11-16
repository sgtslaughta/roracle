import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { apiFetch, seg } from "@/api/client";
import type { DocumentDetail, DocumentPatch, Visibility } from "@/api/types";
import { ChipInput } from "@/features/ask/ChipInput";
import { VIS_LABEL, VIS_ORDER } from "@/features/uploads/status";
import { docTitle } from "@/lib/docTitle";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const userError = (v: string) => (UUID.test(v) ? null : "Enter the person's user id (a UUID).");
const groupError = (v: string) => (v.startsWith("/") ? null : "Group paths start with “/”, for example /Mathematics/Spectral Theory.");

/** Owner/admin: who can see this document. Shares only count while visibility is "shared". */
export function SharingDialog({ doc, open, onOpenChange }: { doc: DocumentDetail; open: boolean; onOpenChange: (o: boolean) => void }) {
  const qc = useQueryClient();
  const [vis, setVis] = useState<Visibility>(doc.visibility);
  const [users, setUsersRaw] = useState<string[]>(doc.share_users ?? []);
  const [groups, setGroupsRaw] = useState<string[]>(doc.share_groups ?? []);
  const [edited, setEdited] = useState({ users: false, groups: false });
  const [confirm, setConfirm] = useState(false);
  const setUsers = (v: string[]) => { setUsersRaw(v); setEdited((e) => ({ ...e, users: true })); setConfirm(false); };
  const setGroups = (v: string[]) => { setGroupsRaw(v); setEdited((e) => ({ ...e, groups: true })); setConfirm(false); };
  const known = doc.share_users !== undefined || doc.share_groups !== undefined;
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
        <DialogDescription>Choose who can see “{docTitle(doc)}”.</DialogDescription>
        <form style={{ display: "grid", gap: 10 }} onSubmit={(e) => {
          e.preventDefault();
          const body: DocumentPatch = { visibility: vis };
          if (vis === "private") { body.share_users = []; body.share_groups = []; }
          if (vis === "shared") {
            if (edited.users) body.share_users = users;
            if (edited.groups) body.share_groups = groups;
            const clears = (edited.users && !users.length) || (edited.groups && !groups.length);
            if (clears && doc.visibility === "shared" && !confirm) { setConfirm(true); return; }
          }
          save.mutate(body);
        }}>
          <label>Visible to
            <select className="inp" value={vis} onChange={(e) => { setVis(e.target.value as Visibility); setConfirm(false); }}>
              {VIS_ORDER.map((v) => <option key={v} value={v}>{VIS_LABEL[v]}</option>)}
            </select>
          </label>
          <p className="sub">Shares only apply when visibility is “Shared”. Switching to “Only me” clears all shares.</p>
          {vis === "shared" && (
            <>
              {!known && <p className="sub">Existing shares aren't listed here; entries you add replace that list.</p>}
              <ChipInput label="Share with users" help="User ids; replaces the current list." values={users} onChange={setUsers} validate={userError} />
              <ChipInput label="Share with groups" help="Group paths; replaces the current list." values={groups} onChange={setGroups} validate={groupError} />
            </>
          )}
          {confirm && <p role="alert">This empties a share list and removes everyone on it. Press Save again to confirm.</p>}
          <Button variant="primary" type="submit" disabled={save.isPending}>Save</Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}
