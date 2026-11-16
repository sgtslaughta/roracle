import { useState } from "react";
import { toast } from "sonner";
import type { AdminUser } from "@/api/admin";
import { ApiError } from "@/api/client";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { OverflowList } from "@/components/OverflowList";
import { Button } from "@/components/ui/button";
import { useDebounced } from "@/lib/useDebounced";
import { fmtTime } from "./ConnectorsPage";
import { USERS_PAGE, useGroups, useRevokePats, useUsers } from "./dataApi";
import { copyAction, RowMenu } from "@/components/RowMenu";

const GROUPS_SHOWN = 50;

function Groups() {
  const { data, isError } = useGroups();
  if (isError) return <p role="alert">Groups couldn't be loaded.</p>;
  if (!data) return null;
  return (
    <section aria-label="Groups">
      <h2>Groups</h2>
      {!data.length && <p className="sub">No groups.</p>}
      <ul className="plain">
        {data.slice(0, GROUPS_SHOWN).map((g) => <li key={g.group} className="rowline">
          <span>{g.group} <small className="sub">{g.members} {g.members === 1 ? "member" : "members"}</small></span>
          <RowMenu label={`Actions for group ${g.group}`} actions={[copyAction("Copy group name", g.group)]} />
        </li>)}
      </ul>
      {data.length > GROUPS_SHOWN && <p className="sub">Showing {GROUPS_SHOWN} of {data.length} groups.</p>}
    </section>
  );
}

export function UsersPage() {
  const [text, setText] = useState("");
  const [page, setPage] = useState(1);
  const [target, setTarget] = useState<AdminUser | null>(null);
  const [last, setLast] = useState<AdminUser | null>(null);
  const ask = (u: AdminUser | null) => { setTarget(u); if (u) setLast(u); };
  const q = useDebounced(text.trim());
  const users = useUsers(q, page);
  const revoke = useRevokePats();
  const pages = Math.max(1, Math.ceil((users.data?.total ?? 0) / USERS_PAGE));

  const go = () =>
    target && revoke.mutate(target.id, {
      onSuccess: () => toast(`Tokens revoked for ${target.username}.`),
      onError: (e) => toast(e instanceof ApiError ? e.message : "The tokens couldn't be revoked. Try again."),
      onSettled: () => ask(null),
    });

  return (
    <div className="admin-page">
      <h2>Users</h2>
      <div className="filters">
        <label>Search users <input type="search" value={text} onChange={(e) => { setText(e.target.value); setPage(1); }} placeholder="Name or email" /></label>
      </div>
      {users.isError && <p role="alert">Users couldn't be loaded. Reload to try again.</p>}
      {users.data && !users.data.items.length && <p className="sub">No users match.</p>}
      {!!users.data?.items.length && (
        <div className="tok-wrap">
          <table className="agent-table admin-table">
            <thead><tr><th>User</th><th>Groups</th><th>Admin</th><th>Last login</th><th><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>
              {users.data.items.map((u) => (
                <tr key={u.id}>
                  <td className="trunc" title={u.username}>{u.username}</td>
                  <td>
                    <OverflowList items={u.groups} max={2} menuLabel={`More groups for ${u.username}`} getLabel={(g) => g}
                      renderItem={(g) => <span key={g} className="cchip" title={g}><span className="cchip-t">{g}</span></span>} />
                  </td>
                  <td>{u.is_admin ? "Yes" : "No"}</td>
                  <td>{u.last_login_at ? fmtTime(u.last_login_at) : "–"}</td>
                  <td>
                    <Button variant="ghost" aria-label={`Revoke tokens for ${u.username}`} onClick={() => ask(u)}>Revoke tokens</Button>
                    <RowMenu kebab={false} label={`Actions for ${u.username}`} actions={[copyAction("Copy username", u.username), { label: "Revoke tokens", danger: true, onSelect: () => ask(u) }]} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="filters">
            <Button disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</Button>
            <span>Page {page} of {pages}</span>
            <Button disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</Button>
          </div>
        </div>
      )}
      <Groups />
      <ConfirmDialog open={!!target} onOpenChange={(o) => !o && ask(null)} title="Revoke all tokens?" confirm="Revoke tokens" onConfirm={go} busy={revoke.isPending}>
        Every personal access token of {(target ?? last)?.username} stops working immediately. They can create new ones.
      </ConfirmDialog>
    </div>
  );
}
