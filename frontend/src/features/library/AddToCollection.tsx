import { Folder } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { useAddToCollection, useCollections } from "./api";

/** Pick one of your collections, or name a new one; the document is added via PUT /collections/{id}/items/{doc}. */
export function AddToCollectionDialog({ docId, open, onOpenChange }: { docId: string; open: boolean; onOpenChange: (o: boolean) => void }) {
  const { data, isError } = useCollections();
  const add = useAddToCollection(docId);
  const [name, setName] = useState("");
  const go = (arg: { id?: string; create?: string }, label: string) =>
    add.mutate(arg, { onSuccess: () => { toast(`Added to “${label}”.`); onOpenChange(false); setName(""); } });
  const n = name.trim();
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="w-[min(440px,calc(100vw-32px))]">
        <DialogTitle>Add to collection</DialogTitle>
        <DialogDescription>Collections are private to you.</DialogDescription>
        {isError && <p role="alert">Your collections couldn't be loaded. Try again.</p>}
        <ul className="menu-list coll-pick" aria-label="Your collections">
          {(data ?? []).map((c) => (
            <li key={c.id}>
              <button className="menu-link" disabled={add.isPending} onClick={() => go({ id: c.id }, c.name)}>
                <Folder aria-hidden /><span>{c.name}</span><small className="sub">{c.count}</small>
              </button>
            </li>
          ))}
          {data && !data.length && <li className="sub">No collections yet. Create one below.</li>}
        </ul>
        <form className="row2" onSubmit={(e) => { e.preventDefault(); if (!n) return; const same = data?.find((c) => c.name.toLowerCase() === n.toLowerCase()); go(same ? { id: same.id } : { create: n }, same?.name ?? n); }}>
          <input aria-label="New collection name" placeholder="New collection" maxLength={100} value={name} onChange={(e) => setName(e.target.value)} />
          <Button variant="primary" type="submit" disabled={!n || add.isPending}>Create and add</Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}
