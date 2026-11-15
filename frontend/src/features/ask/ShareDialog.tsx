import { useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { useShareChat } from "./api";
import { ChipInput } from "./ChipInput";

const groupError = (v: string) => (v.startsWith("/") ? null : "Group paths start with “/”, for example /Mathematics/Spectral Theory.");

export function ShareDialog({ chatId, open, onOpenChange }: { chatId: string; open: boolean; onOpenChange: (o: boolean) => void }) {
  const share = useShareChat(chatId);
  const [groups, setGroups] = useState<string[]>([]);
  const [users, setUsers] = useState<string[]>([]);
  const gRef = useRef<() => string[] | null>(null);
  const uRef = useRef<() => string[] | null>(null);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(`${location.origin}/ask/${chatId}`);
      toast("Link copied. It opens read-only for people you share with.");
    } catch { toast("Copying isn't available here."); }
  };
  const save = () => {
    const g = gRef.current ? gRef.current() : groups, u = uRef.current ? uRef.current() : users;
    if (!g || !u) return;
    share.mutate({ users: u, groups: g }, {
      onSuccess: () => { toast("Sharing updated."); onOpenChange(false); },
      onError: () => toast("Sharing couldn't be updated. Try again."),
    });
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogTitle>Share this chat</DialogTitle>
        <DialogDescription>People you share with can read this chat. They see a citation only if they can see its source.</DialogDescription>
        <ChipInput label="Groups" values={groups} onChange={setGroups} validate={groupError} commitRef={gRef} placeholder="/Mathematics/Spectral Theory" />
        <ChipInput label="People (user IDs)" help="Paste a user ID. A people picker comes with the admin section." values={users} onChange={setUsers} commitRef={uRef} />
        <p className="chipin-help">Saving replaces the current list.</p>
        <div className="foot">
          <Button variant="ghost" onClick={() => void copy()}>Copy link</Button>
          <Button variant="primary" onClick={save} disabled={share.isPending}>Save</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
