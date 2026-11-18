import { X } from "lucide-react";
import type { DraftDetail } from "@/api/drafts";
import { OverflowList } from "@/components/OverflowList";
import { RowMenu } from "@/components/RowMenu";
import { DirectoryPicker } from "@/components/DirectoryPicker";
import { usePeopleNames } from "@/components/PeopleInput";
import type { Person } from "@/api/types";
import { draftError, useSetCoauthors } from "./api";

/** Co-authors picked by name (people) or org unit (groups). Only the owner edits; others see the list. */
export function CoauthorsPanel({ draft, owner }: { draft: DraftDetail; owner: boolean }) {
  const set = useSetCoauthors(draft.id);
  const { users, groups } = draft.coauthors;
  const names = usePeopleNames(users);
  const entries = [...users.map((v) => ({ kind: "users" as const, v })), ...groups.map((v) => ({ kind: "groups" as const, v }))];
  const label = (e: (typeof entries)[number]) => (e.kind === "users" ? names.get(e.v) ?? "Unknown person" : e.v);
  const write = (next: { users: string[]; groups: string[] }) => set.mutate(next);
  const remove = (e: (typeof entries)[number]) => write({ users: users.filter((u) => e.kind !== "users" || u !== e.v), groups: groups.filter((g) => e.kind !== "groups" || g !== e.v) });
  return (
    <div className="dpanel">
      {!entries.length && <p className="sub">No co-authors yet. Co-authors can edit and run checks; only you can submit.</p>}
      <OverflowList className="vlist" items={entries} max={10} menuLabel="More co-authors" getLabel={label}
        renderItem={(e) => (
          <div className="vrow" data-row key={`${e.kind}:${e.v}`}>
            <span><span className="badge-o">{e.kind === "users" ? "Person" : "Org unit"}</span> {label(e)}</span>
            {owner && <RowMenu kebab={false} label={`Actions for ${label(e)}`} actions={[{ label: "Remove co-author", danger: true, onSelect: () => remove(e) }]} />}
            {owner && <button className="icon-btn" aria-label={`Remove ${label(e)}`} onClick={() => remove(e)}><X aria-hidden /></button>}
          </div>
        )} />
      {owner && (
        <div className="dform">
          <DirectoryPicker<Person> kind="users" label="Add a person" exclude={users} onPick={(p) => write({ users: [...users, p.id], groups })} />
          <DirectoryPicker<string> kind="groups" label="Add an org unit" exclude={groups} onPick={(g) => write({ users, groups: [...groups, g] })} />
        </div>
      )}
      {set.isError && <p role="alert" className="derr">{draftError(set.error)}</p>}
    </div>
  );
}
