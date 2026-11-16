import { Link, useRouter } from "@tanstack/react-router";
import { Bell as BellIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { showNote } from "@/features/notes/api";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useLatestNotifications, useMarkRead } from "./api";
import { describe, type AppNotification } from "./describe";

export const countLabel = (n: number) => (n > 99 ? "99+" : String(n));

/** Clicking a notification marks it read and follows its in-app link (paths not typed: admin routes may not exist yet). */
export function useOpenNotification() {
  const router = useRouter();
  const mark = useMarkRead();
  return (n: AppNotification) => {
    if (!n.read_at) mark.mutate({ ids: [n.id] });
    const { to, note } = describe(n.kind, n.payload);
    if (note) showNote(note.docId, note.noteId); // the reader opens over the current page, straight on its Notes tab
    else if (to) router.history.push(to);
  };
}

export function NotificationRow({ n, onOpen }: { n: AppNotification; onOpen: (n: AppNotification) => void }) {
  const { text } = describe(n.kind, n.payload);
  return (
    <button className={cn("nrow", !n.read_at && "unread")} onClick={() => onOpen(n)}>
      <span className="nrow-t">{text}</span>
      <time className="sub" dateTime={n.created_at}>{new Date(n.created_at).toLocaleString()}</time>
      {!n.read_at && <span className="sr-only">Unread</span>}
    </button>
  );
}

export function Bell() {
  const [open, setOpen] = useState(false);
  const { data } = useLatestNotifications();
  const mark = useMarkRead();
  const openOne = useOpenNotification();
  const unread = data?.unread ?? 0;
  const items = data?.items ?? [];
  const qc = useQueryClient();
  const newest = items.reduce((m, n) => (n.kind === "proposal.new" && !n.read_at ? Math.max(m, Number(n.id)) : m), 0);
  const seen = useRef(0);
  // an agent proposed something newer than we last saw: refresh the inbox and the sidebar badge
  useEffect(() => { if (newest > seen.current) { seen.current = newest; void qc.invalidateQueries({ queryKey: ["proposals"] }); } }, [newest, qc]);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button className="icon-btn keep bell" aria-label={unread ? `Notifications, ${unread} unread` : "Notifications"}>
          <BellIcon aria-hidden />
          {unread > 0 && <b className="bell-badge" aria-hidden>{countLabel(unread)}</b>}
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" className="bellpop" aria-label="Notifications">
        <div className="bellhead">
          <b>Notifications</b>
          <Button variant="ghost" disabled={!unread || mark.isPending} onClick={() => mark.mutate({ all: true })}>Mark all read</Button>
        </div>
        {items.length
          ? <ul className="menu-list">{items.map((n) => <li key={n.id}><NotificationRow n={n} onOpen={(x) => { setOpen(false); openOne(x); }} /></li>)}</ul>
          : <p className="sub bellempty">You're all caught up.</p>}
        <Link to="/notifications" className="menu-link bellall" onClick={() => setOpen(false)}>See all</Link>
      </PopoverContent>
    </Popover>
  );
}
