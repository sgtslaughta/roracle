import { Link } from "@tanstack/react-router";
import { Folder, MessageCircle, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { OverflowList } from "@/components/OverflowList";
import { useChats } from "@/features/ask/api";
import { useCollections } from "@/features/library/api";
import { useUploadAttention } from "@/features/uploads/api";
import { NAV } from "./nav";

export function Sidebar() {
  const { data: collections } = useCollections();
  const { data: chats, hasNextPage, isFetchingNextPage, fetchNextPage } = useChats();
  const attention = useUploadAttention();
  return (
    <aside className="side">
      <nav className="nav" aria-label="Main">
        {NAV.map(({ to, label, icon: Icon }) => (
          <Link key={to} to={to} activeProps={{ "aria-current": "page" }} inactiveProps={{ "aria-current": undefined }}>
            <Icon aria-hidden />
            <span>{label}</span>
            {to === "/uploads" && attention > 0 && <b className="badge" aria-label={`${attention} need attention`}>{attention}</b>}
          </Link>
        ))}
      </nav>
      <div className="sgroup">
        <h5>Recent chats</h5>
        <nav className="nav" aria-label="Recent chats">
          <OverflowList
            items={chats ?? []} max={5} layout="stack" menuLabel="More chats"
            footer={hasNextPage && <Button variant="ghost" disabled={isFetchingNextPage} onClick={() => void fetchNextPage()}>Load more</Button>}
            getLabel={(c) => c.title} moreLabel={(k) => `+${k} more`}
            renderItem={(c) => (
              <Link key={c.id} to="/ask/$chatId" params={{ chatId: c.id }} title={c.title} activeProps={{ "aria-current": "page" }} inactiveProps={{ "aria-current": undefined }}>
                <MessageCircle aria-hidden />
                <span>{c.title}</span>
              </Link>
            )}
          />
          <Link to="/ask" activeOptions={{ exact: true }} activeProps={{ "aria-current": "page" }} inactiveProps={{ "aria-current": undefined }}>
            <Plus aria-hidden />
            <span>New chat</span>
          </Link>
        </nav>
      </div>
      {!!collections?.length && (
        <div className="sgroup">
          <h5>Collections</h5>
          <nav className="nav" aria-label="Collections">
            <OverflowList
              items={collections} max={6} layout="stack" menuLabel="More collections" getLabel={(c) => c.name} moreLabel={(k) => `+${k} more`}
              renderItem={(c) => (
                <Link key={c.id} to="/library" search={{ collection: [c.id] }} title={c.name}>
                  <Folder aria-hidden />
                  <span>{c.name}</span>
                </Link>
              )}
            />
          </nav>
        </div>
      )}
    </aside>
  );
}
