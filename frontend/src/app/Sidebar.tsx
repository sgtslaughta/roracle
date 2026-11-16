import { Link, useNavigate } from "@tanstack/react-router";
import { Folder, History, MessageCircle, Plus, Shield } from "lucide-react";
import { Button } from "@/components/ui/button";
import { OverflowList } from "@/components/OverflowList";
import { copyAction, RowMenu } from "@/components/RowMenu";
import { useChats } from "@/features/ask/api";
import { useCollections } from "@/features/library/api";
import { useUploadAttention } from "@/features/uploads/api";
import { useProposals } from "@/features/proposals/api";
import { useMe } from "@/features/library/useMe";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { NAV } from "./nav";

export function Sidebar() {
  const navigate = useNavigate();
  const { data: collections } = useCollections();
  const { data: chats, hasNextPage, isFetchingNextPage, fetchNextPage } = useChats();
  const { data: me } = useMe();
  const attention = useUploadAttention();
  const waiting = useProposals("pending").data?.total ?? 0;
  return (
    <aside className="side">
      <nav className="nav" aria-label="Main">
        {NAV.map(({ to, label, icon: Icon }) => {
          const n = to === "/uploads" ? attention : to === "/proposals" ? waiting : 0;
          const name = n > 0 ? `${label}, ${n > 99 ? "99+" : n} ${to === "/uploads" ? "need attention" : "waiting"}` : label;
          return (
          <Link key={to} to={to} aria-label={name} title={name} activeProps={{ "aria-current": "page" }} inactiveProps={{ "aria-current": undefined }}>
            <Icon aria-hidden />
            <span>{label}</span>
            {to === "/uploads" && attention > 0 && <b className="badge" aria-label={`${attention} need attention`}>{attention}</b>}
            {to === "/proposals" && waiting > 0 && <b className="badge" aria-label={`${waiting} waiting`}>{waiting > 99 ? "99+" : waiting}</b>}
          </Link>
          );
        })}
        {me?.is_admin && (
          <Link to="/admin" aria-label="Admin" title="Admin" activeProps={{ "aria-current": "page" }} inactiveProps={{ "aria-current": undefined }}>
            <Shield aria-hidden />
            <span>Admin</span>
          </Link>
        )}
        <Popover>
          <PopoverTrigger asChild>
            <button className="rail-only" aria-label="Chats and collections" title="Chats and collections"><History aria-hidden /></button>
          </PopoverTrigger>
          <PopoverContent side="top" align="start" collisionPadding={8} className="rail-pop">
            <h5>Recent chats</h5>
            <ul className="menu-list">
              {(chats ?? []).map((c) => (
                <li key={c.id}><Link to="/ask/$chatId" params={{ chatId: c.id }} className="menu-link" title={c.title}><MessageCircle aria-hidden /><span>{c.title}</span></Link></li>
              ))}
              <li><Link to="/ask" className="menu-link"><Plus aria-hidden /><span>New chat</span></Link></li>
            </ul>
            {hasNextPage && <Button variant="ghost" disabled={isFetchingNextPage} onClick={() => void fetchNextPage()}>Load more</Button>}
            {!!collections?.length && (
              <>
                <h5>Collections</h5>
                <ul className="menu-list">
                  {collections.map((c) => (
                    <li key={c.id}><Link to="/library" search={{ collection: [c.id] }} className="menu-link" title={c.name}><Folder aria-hidden /><span>{c.name}</span></Link></li>
                  ))}
                </ul>
              </>
            )}
          </PopoverContent>
        </Popover>
      </nav>
      <div className="sgroup">
        <h5>Recent chats</h5>
        <nav className="nav" aria-label="Recent chats">
          <OverflowList
            items={chats ?? []} max={5} layout="stack" menuLabel="More chats"
            footer={hasNextPage && <Button variant="ghost" disabled={isFetchingNextPage} onClick={() => void fetchNextPage()}>Load more</Button>}
            getLabel={(c) => c.title} moreLabel={(k) => `+${k} more`}
            renderItem={(c) => (
              <div key={c.id} className="rowline" data-row>
                <Link to="/ask/$chatId" params={{ chatId: c.id }} title={c.title} activeProps={{ "aria-current": "page" }} inactiveProps={{ "aria-current": undefined }}>
                  <MessageCircle aria-hidden />
                  <span>{c.title}</span>
                </Link>
                <RowMenu label={`Actions for chat ${c.title}`} actions={[
                  { label: "Open", onSelect: () => void navigate({ to: "/ask/$chatId", params: { chatId: c.id } }) },
                  copyAction("Copy link", `${window.location.origin}/ask/${encodeURIComponent(c.id)}`),
                ]} />
              </div>
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
                <div key={c.id} className="rowline" data-row>
                  <Link to="/library" search={{ collection: [c.id] }} title={c.name}>
                    <Folder aria-hidden />
                    <span>{c.name}</span>
                  </Link>
                  <RowMenu label={`Actions for collection ${c.name}`} actions={[
                    { label: "Show in Library", onSelect: () => void navigate({ to: "/library", search: { collection: [c.id] } }) },
                    copyAction("Copy name", c.name),
                  ]} />
                </div>
              )}
            />
          </nav>
        </div>
      )}
    </aside>
  );
}
