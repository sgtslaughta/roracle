import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { apiFetch, seg } from "@/api/client";
import type { DocumentDetail, DocumentPatch, Person, Visibility } from "@/api/types";
import { ChipList, DirectoryPicker } from "@/components/DirectoryPicker";
import { OverflowList } from "@/components/OverflowList";
import { VIS_LABEL, VIS_ORDER } from "@/features/uploads/status";
import { docTitle } from "@/lib/docTitle";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";

/** Owner/admin: who can see this document. Starts from the current shares (GET /documents/{id}); shares only count while visibility is "shared". */
export function SharingDialog({ doc, open, onOpenChange }: { doc: DocumentDetail; open: boolean; onOpenChange: (o: boolean) => void }) {
  const qc = useQueryClient();
  const [vis, setVis] = useState<Visibility>(doc.visibility);
  const [users, setUsersRaw] = useState<Person[]>(doc.share_users ?? []);
  const [groups, setGroupsRaw] = useState<string[]>(doc.share_groups ?? []);
  const [edited, setEdited] = useState({ users: false, groups: false });
  const [confirm, setConfirm] = useState(false);
  const setUsers = (v: Person[]) => { setUsersRaw(v); setEdited((e) => ({ ...e, users: true })); setConfirm(false); };
  const setGroups = (v: string[]) => { setGroupsRaw(v); setEdited((e) => ({ ...e, groups: true })); setConfirm(false); };
  const names = new Map(users.map((u) => [u.id, u.username || u.id]));
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
        <form className="dform" onSubmit={(e) => {
          e.preventDefault();
          const body: DocumentPatch = { visibility: vis };
          if (vis === "private") { body.share_users = []; body.share_groups = []; }
          if (vis === "shared") {
            if (edited.users) body.share_users = users.map((u) => u.id);
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
              <DirectoryPicker<Person> kind="users" label="Share with users" exclude={users.map((u) => u.id)} onPick={(u) => setUsers([...users, u])} />
              <ChipList items={users.map((u) => u.id)} label="users" getName={(id) => names.get(id) ?? id} onRemove={(id) => setUsers(users.filter((u) => u.id !== id))} />
              <DirectoryPicker<string> kind="groups" label="Share with groups" exclude={groups} onPick={(g) => setGroups([...groups, g])} />
              <ChipList items={groups} label="groups" onRemove={(g) => setGroups(groups.filter((x) => x !== g))} />
            </>
          )}
          {!!doc.coauthors?.length && (
            <div><span className="sub">Co-authors (managed on the draft): </span>
              <OverflowList items={doc.coauthors} max={4} menuLabel="More co-authors" getLabel={(c) => c.username || c.id} renderItem={(c) => <span key={c.id} className="badge-o">{c.username || c.id}</span>} />
            </div>
          )}
          {confirm && <p role="alert">This empties a share list and removes everyone on it. Press Save again to confirm.</p>}
          <div className="foot">
            <Button variant="ghost" type="button" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button variant="primary" type="submit" disabled={save.isPending}>Save</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
