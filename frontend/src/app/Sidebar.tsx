import { chatTitle } from "@/lib/cite";
import { useState } from "react";
import { toast } from "sonner";
import { errorText } from "@/api/client";
import { Link, useNavigate, useParams } from "@tanstack/react-router";
import { Folder, History, MessageCircle, Plus, Shield } from "lucide-react";
import { Button } from "@/components/ui/button";
import { OverflowList } from "@/components/OverflowList";
import { copyAction, RowMenu } from "@/components/RowMenu";
import { useChats, useHideChat } from "@/features/ask/api";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { useCollections } from "@/features/library/api";
import { useUploadAttention } from "@/features/uploads/api";
import { useProposals } from "@/features/proposals/api";
import { useMe } from "@/features/library/useMe";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { NAV } from "./nav";
import { CollectionDialogs, type CollectionEdit } from "./CollectionDialogs";

export function Sidebar() {
  const navigate = useNavigate();
  const { data: collections } = useCollections();
  const { data: chats, hasNextPage, isFetchingNextPage, fetchNextPage } = useChats();
  const { data: me } = useMe();
  const [railOpen, setRailOpen] = useState(false);
  const [edit, setEdit] = useState<CollectionEdit | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);
  const hide = useHideChat();
  const { chatId } = useParams({ strict: false }) as { chatId?: string };
  const close = () => setRailOpen(false);
  const attention = useUploadAttention();
  const waiting = useProposals("pending").data?.total ?? 0;
  /** Chat / collection entries; the rail popover variant is context-menu only and closes the popover on navigation. */
  const chatRow = (c: Parameters<typeof chatTitle>[0] & { id: string }, rail = false) => (
    <div key={c.id} className="rowline" data-row>
      <Link to="/ask/$chatId" params={{ chatId: c.id }} title={chatTitle(c)} onClick={rail ? close : undefined}
        {...(rail ? { className: "menu-link" } : { activeProps: { "aria-current": "page" as const }, inactiveProps: { "aria-current": undefined } })}>
        <MessageCircle aria-hidden />
        <span>{chatTitle(c)}</span>
      </Link>
      <RowMenu label={`Actions for chat ${chatTitle(c)}`} actions={[
        { label: "Open", onSelect: () => { close(); void navigate({ to: "/ask/$chatId", params: { chatId: c.id } }); } },
        copyAction("Copy link", `${window.location.origin}/ask/${encodeURIComponent(c.id)}`),
        { label: "Remove from my chats", danger: true, onSelect: () => { close(); setRemoving(c.id); } },
      ]} />
    </div>
  );
  const collectionRow = (c: { id: string; name: string }, rail = false) => (
    <div key={c.id} className="rowline" data-row>
      <Link to="/library" search={{ collection: [c.id] }} title={c.name} onClick={rail ? close : undefined} className={rail ? "menu-link" : undefined}>
        <Folder aria-hidden />
        <span>{c.name}</span>
      </Link>
      <RowMenu label={`Actions for collection ${c.name}`} actions={[
        { label: "Show in Library", onSelect: () => { close(); void navigate({ to: "/library", search: { collection: [c.id] } }); } },
        { label: "Rename…", onSelect: () => { close(); setEdit({ ...c, mode: "rename" }); } },
        { label: "Delete…", danger: true, onSelect: () => { close(); setEdit({ ...c, mode: "delete" }); } },
        copyAction("Copy name", c.name),
      ]} />
    </div>
  );
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
        <Popover open={railOpen} onOpenChange={setRailOpen}>
          <PopoverTrigger asChild>
            <button className="rail-only" aria-label="Chats and collections" title="Chats and collections"><History aria-hidden /></button>
          </PopoverTrigger>
          <PopoverContent side="top" align="start" collisionPadding={8} className="rail-pop">
            <h5>Recent chats</h5>
            <OverflowList
              items={chats ?? []} max={5} layout="stack" menuLabel="More chats" getLabel={chatTitle} moreLabel={(k) => `+${k} more`}
              footer={hasNextPage && <Button variant="ghost" disabled={isFetchingNextPage} onClick={() => void fetchNextPage()}>Load more</Button>}
              renderItem={(c) => chatRow(c, true)}
            />
            <Link to="/ask" className="menu-link" onClick={close}><Plus aria-hidden /><span>New chat</span></Link>
            {!!collections?.length && (
              <>
                <h5>Collections</h5>
                <OverflowList
                  items={collections} max={6} layout="stack" menuLabel="More collections" getLabel={(c) => c.name} moreLabel={(k) => `+${k} more`}
                  renderItem={(c) => collectionRow(c, true)}
                />
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
            getLabel={chatTitle} moreLabel={(k) => `+${k} more`}
            renderItem={(c) => chatRow(c)}
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
              renderItem={(c) => collectionRow(c)}
            />
          </nav>
        </div>
      )}
      <CollectionDialogs edit={edit} onClose={() => setEdit(null)} />
      <ConfirmDialog open={!!removing} onOpenChange={(o) => !o && setRemoving(null)} title="Remove from my chats?" confirm="Remove" busy={hide.isPending}
        onConfirm={() => removing && hide.mutate(removing, {
          onSuccess: () => { if (removing === chatId) void navigate({ to: "/ask" }); setRemoving(null); },
          onError: (e) => { toast(errorText(e)); setRemoving(null); },
        })}>
        Remove this chat from your list? Shared links keep working.
      </ConfirmDialog>
    </aside>
  );
}
