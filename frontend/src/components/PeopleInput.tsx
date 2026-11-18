import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { apiFetch } from "@/api/client";
import type { Person } from "@/api/types";
import { ChipList, DirectoryPicker } from "./DirectoryPicker";

/** Usernames for ids already on a list (GET /directory/users/lookup); unknown ids stay as they are. */
export function usePeopleNames(ids: string[]) {
  const key = [...ids].sort();
  const { data } = useQuery({
    queryKey: ["directory", "lookup", key], enabled: key.length > 0, staleTime: 60_000,
    queryFn: async () => (await apiFetch<{ items: Person[] }>(`/directory/users/lookup?${key.map((i) => `ids=${encodeURIComponent(i)}`).join("&")}`)).items,
  });
  return new Map((data ?? []).map((p) => [p.id, p.username]));
}

/** People picked by name (directory type-ahead); `values` are user ids, shown as usernames. */
/** `full`: a message shown instead of the picker when nobody more can be added. */
export function PeopleInput({ label, values, onChange, full }: { label: string; values: string[]; onChange: (ids: string[]) => void; full?: string | null }) {
  const [picked, setPicked] = useState<Map<string, string>>(new Map());
  const looked = usePeopleNames(values.filter((id) => !picked.has(id)));
  const name = (id: string) => picked.get(id) ?? looked.get(id) ?? "Unknown person";
  return (
    <div className="chipin">
      {full ? <p className="chipin-help">{full}</p> : (
        <DirectoryPicker<Person> kind="users" label={label} exclude={values} onPick={(p) => { setPicked((m) => new Map(m).set(p.id, p.username)); onChange([...values, p.id]); }} />
      )}
      <ChipList items={values} label={label} getName={name} onRemove={(id) => onChange(values.filter((v) => v !== id))} />
    </div>
  );
}
