import { X } from "lucide-react";
import { useState } from "react";
import type { DraftDetail } from "@/api/drafts";
import { OverflowList } from "@/components/OverflowList";
import { RowMenu } from "@/components/RowMenu";
import { Button } from "@/components/ui/button";
import { draftError, useSetCoauthors } from "./api";

/** Co-authors by typed id (there is no user lookup endpoint). Only the owner edits; others see the list. */
export function CoauthorsPanel({ draft, owner }: { draft: DraftDetail; owner: boolean }) {
  const set = useSetCoauthors(draft.id);
  const [kind, setKind] = useState<"users" | "groups">("users");
  const [value, setValue] = useState("");
  const { users, groups } = draft.coauthors;
  const entries = [...users.map((v) => ({ kind: "users" as const, v })), ...groups.map((v) => ({ kind: "groups" as const, v }))];
  const write = (next: { users: string[]; groups: string[] }) => set.mutate(next);
  const remove = (e: (typeof entries)[number]) => write({ users: users.filter((u) => e.kind !== "users" || u !== e.v), groups: groups.filter((g) => e.kind !== "groups" || g !== e.v) });
  const add = (ev: React.FormEvent) => {
    ev.preventDefault();
    const v = value.trim();
    if (!v) return;
    write({ users: kind === "users" ? [...new Set([...users, v])] : users, groups: kind === "groups" ? [...new Set([...groups, v])] : groups });
    setValue("");
  };
  return (
    <div className="dpanel">
      {!entries.length && <p className="sub">No co-authors yet. Co-authors can edit and run checks; only you can submit.</p>}
      <OverflowList className="vlist" items={entries} max={10} menuLabel="More co-authors" getLabel={(e) => e.v}
        renderItem={(e) => (
          <div className="vrow" data-row key={`${e.kind}:${e.v}`}>
            <span><span className="badge-o">{e.kind === "users" ? "User" : "Org unit"}</span> <code>{e.v}</code></span>
            {owner && <RowMenu kebab={false} label={`Actions for ${e.v}`} actions={[{ label: "Remove co-author", danger: true, onSelect: () => remove(e) }]} />}
            {owner && <button className="icon-btn" aria-label={`Remove ${e.v}`} onClick={() => remove(e)}><X aria-hidden /></button>}
          </div>
        )} />
      {owner && (
        <form className="dform inline" onSubmit={add}>
          <select className="inp" aria-label="Co-author type" value={kind} onChange={(e) => setKind(e.target.value as "users" | "groups")}>
            <option value="users">User id</option><option value="groups">Org unit path</option>
          </select>
          <input className="inp" aria-label="Co-author id" placeholder={kind === "users" ? "User id" : "/Mathematics/Spectral Theory"} value={value} onChange={(e) => setValue(e.target.value)} />
          <Button type="submit" disabled={!value.trim() || set.isPending}>Add</Button>
        </form>
      )}
      {set.isError && <p role="alert" className="derr">{draftError(set.error)}</p>}
    </div>
  );
}
