import { BellOff } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { useMarkRead, useNotificationList } from "./api";
import { NotificationRow, useOpenNotification } from "./Bell";

export function NotificationsPage() {
  const [unreadOnly, setUnreadOnly] = useState(false);
  const { data, isPending, isError, hasNextPage, isFetchingNextPage, fetchNextPage } = useNotificationList(unreadOnly);
  const mark = useMarkRead();
  const openOne = useOpenNotification();
  const items = data?.pages.flatMap((p) => p.items) ?? [];
  const unread = data?.pages[0]?.unread ?? 0;
  return (
    <section className="view">
      <div className="uhead-row">
        <div>
          <h1>Notifications</h1>
          <p className="sub">{unread ? `${unread} unread` : "All read"}</p>
        </div>
        <div className="row2">
          <label className="row2 sub">
            <Checkbox checked={unreadOnly} onCheckedChange={(c) => setUnreadOnly(c === true)} />Unread only
          </label>
          <Button disabled={!unread || mark.isPending} onClick={() => mark.mutate({ all: true })}>Mark all read</Button>
        </div>
      </div>
      {isPending ? <p className="sub">Loading…</p>
        : isError ? <p className="sub" role="alert">Notifications couldn't be loaded. Try again.</p>
        : !items.length ? <div className="empty"><BellOff aria-hidden /><p>{unreadOnly ? "No unread notifications." : "No notifications yet."}</p></div>
        : (
          <ul className="ntfy">
            {items.map((n) => <li key={n.id}><NotificationRow n={n} onOpen={openOne} /></li>)}
          </ul>
        )}
      {hasNextPage && <Button variant="ghost" disabled={isFetchingNextPage} onClick={() => void fetchNextPage()}>Load more</Button>}
    </section>
  );
}
