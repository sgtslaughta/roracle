import { useRouter } from "@tanstack/react-router";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { ApiError } from "@/api/client";
import type { Shares } from "@/api/chat";
import { useShareChat, useShares } from "./api";
import { ChipInput } from "./ChipInput";
import { PeopleInput } from "@/components/PeopleInput";

const groupError = (v: string) => (v.startsWith("/") ? null : "Group paths start with “/”, for example /Mathematics/Spectral Theory.");

export function ShareDialog({ chatId, open, onOpenChange }: { chatId: string; open: boolean; onOpenChange: (o: boolean) => void }) {
  const shares = useShares(chatId, open);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogTitle>Share this chat</DialogTitle>
        <DialogDescription>People you share with can read this chat. They see a citation only if they can see its source.</DialogDescription>
        {shares.data ? <ShareForm chatId={chatId} initial={shares.data} onDone={() => onOpenChange(false)} /> : shares.isError ? (
          <p role="alert">Sharing couldn't be loaded. Close this and try again.</p>
        ) : <p className="sub" role="status">Loading…</p>}
      </DialogContent>
    </Dialog>
  );
}

function ShareForm({ chatId, initial, onDone }: { chatId: string; initial: Shares; onDone: () => void }) {
  const share = useShareChat(chatId);
  const router = useRouter();
  const [groups, setGroups] = useState(initial.groups);
  const [users, setUsers] = useState(initial.users);
  const gRef = useRef<() => string[] | null>(null);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(location.origin + router.buildLocation({ to: "/ask/$chatId", params: { chatId } }).href);
      toast("Link copied. It opens read-only for people you share with.");
    } catch { toast("Copying isn't available here."); }
  };
  const save = () => {
    const g = gRef.current ? gRef.current() : groups, u = users;
    if (!g || !u) return;
    share.mutate({ users: u, groups: g }, {
      onSuccess: () => { toast("Sharing updated."); onDone(); },
      onError: (e) => toast(e instanceof ApiError && e.status === 422 ? e.message : "Sharing couldn't be updated. Try again."),
    });
  };
  return (
    <>
      <ChipInput label="Groups" values={groups} onChange={setGroups} validate={groupError} commitRef={gRef} placeholder="/Mathematics/Spectral Theory" />
      <PeopleInput label="People" values={users} onChange={setUsers} />
      <p className="chipin-help">Saving replaces the current list.</p>
      <div className="foot">
        <Button variant="ghost" onClick={() => void copy()}>Copy link</Button>
        <Button variant="primary" onClick={save} disabled={share.isPending}>Save</Button>
      </div>
    </>
  );
}
