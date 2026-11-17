import { useQuery } from "@tanstack/react-query";
import { X } from "lucide-react";
import { useId, useState } from "react";
import { apiFetch } from "@/api/client";
import type { Person } from "@/api/types";
import { OverflowList } from "@/components/OverflowList";
import { useDebounced } from "@/lib/useDebounced";

type Kind = "users" | "groups";
interface Props<T> { kind: Kind; label: string; exclude: string[]; onPick: (v: T) => void }

/** Type-ahead over GET /directory/users?q= or /directory/groups?q= (min 2 characters; at most 10 matches). */
export function DirectoryPicker<T extends Person | string>({ kind, label, exclude, onPick }: Props<T>) {
  const id = useId();
  const [text, setText] = useState("");
  const q = useDebounced(text.trim(), 200);
  const { data, isFetching } = useQuery({
    queryKey: ["directory", kind, q], enabled: q.length >= 2, staleTime: 30_000,
    queryFn: async () => (await apiFetch<{ items: T[] }>(`/directory/${kind}?q=${encodeURIComponent(q)}`)).items,
  });
  const key = (v: T) => (typeof v === "string" ? v : v.id);
  const name = (v: T) => (typeof v === "string" ? v : v.username);
  const items = (data ?? []).filter((v) => !exclude.includes(key(v)));
  return (
    <div className="dirpick">
      <label htmlFor={id}>{label}</label>
      <input id={id} className="inp" autoComplete="off" placeholder="Type at least 2 letters" value={text} onChange={(e) => setText(e.target.value)} />
      {q.length >= 2 && (
        <ul className="dirlist" aria-label={`${label} matches`}>
          {items.map((v) => (
            <li key={key(v)}><button type="button" onClick={() => { onPick(v); setText(""); }}>{name(v)}</button></li>
          ))}
          {!items.length && !isFetching && <li className="sub">No matches.</li>}
        </ul>
      )}
    </div>
  );
}

/** Removable chips, at most 6 inline then +K (shared OverflowList). */
export function ChipList({ items, label, onRemove, getKey, getName }: { items: string[]; label: string; onRemove: (v: string) => void; getKey?: (v: string) => string; getName?: (v: string) => string }) {
  if (!items.length) return null;
  const name = getName ?? ((v: string) => v);
  return (
    <OverflowList
      items={items} max={6} menuLabel={`More ${label.toLowerCase()}`} getLabel={name}
      renderItem={(v) => (
        <span key={getKey?.(v) ?? v} className="cchip" title={name(v)}>
          <span className="cchip-t">{name(v)}</span>
          <button type="button" aria-label={`Remove ${name(v)}`} onClick={() => onRemove(v)}><X aria-hidden /></button>
        </span>
      )}
    />
  );
}
