import { Bookmark } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useNavigate } from "@tanstack/react-router";
import type { SavedSearch } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { RowMenu } from "@/components/RowMenu";
import { parseSearch, validateLibrarySearch, type LibrarySearch } from "@/lib/searchParams";
import { buildSentence, sentenceText } from "./querySentence";
import { useSentenceNames } from "./QuerySentence";
import { QUERY_MAX, TOO_LONG, saveError, savedQuery, useDeleteSavedSearch, useRenameSavedSearch, useSaveSearch, useSavedSearches } from "./savedSearches";

function Row({ s, onOpen }: { s: SavedSearch; onOpen: () => void }) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(s.name);
  const rename = useRenameSavedSearch();
  const del = useDeleteSavedSearch();
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const n = name.trim();
    if (!n || n === s.name) return setEditing(false);
    rename.mutate({ id: s.id, name: n }, { onSuccess: () => setEditing(false) });
  };
  return (
    <li className="ssrow">
      {editing ? (
        <form onSubmit={submit} onKeyDown={(e) => e.key === "Escape" && (e.stopPropagation(), setEditing(false))}>
          <input aria-label={`Rename ${s.name}`} value={name} maxLength={100} autoFocus onChange={(e) => setName(e.target.value)} />
          {rename.isError && <p className="sserr" role="alert">{saveError(rename.error)}</p>}
        </form>
      ) : (
        <>
          <button type="button" className="ssopen" onClick={onOpen}>{s.name}</button>
          <RowMenu label={`Actions for ${s.name}`} actions={[
            { label: "Rename", onSelect: () => { setName(s.name); rename.reset(); setEditing(true); } },
            { label: "Delete", danger: true, onSelect: () => del.mutate(s) },
          ]} />
        </>
      )}
    </li>
  );
}

/** Save the current search under a name, and reopen, rename or delete saved ones. */
export function SavedSearchesMenu({ search }: { search: LibrarySearch }) {
  const navigate = useNavigate();
  const names = useSentenceNames(search.cites);
  const { data } = useSavedSearches();
  const save = useSaveSearch();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const onOpenChange = (o: boolean) => {
    if (o) { setName(sentenceText(buildSentence(search, names)).slice(0, 100)); save.reset(); }
    setOpen(o);
  };
  const query = savedQuery(search);
  const tooLong = query.length > QUERY_MAX;
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (name.trim() && !tooLong) save.mutate({ name: name.trim(), query });
  };
  const openSaved = (s: SavedSearch) => {
    setOpen(false);
    void navigate({ to: "/library", search: validateLibrarySearch(parseSearch(s.query)) as never });
  };
  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      <PopoverTrigger asChild><Button title="Saved searches"><Bookmark aria-hidden /><span className="tlbl">Saved searches</span></Button></PopoverTrigger>
      <PopoverContent className="ssmenu" role="dialog" aria-label="Saved searches">
        <form onSubmit={submit} className="ssform">
          <label>Name<input aria-label="Name" value={name} maxLength={100} onChange={(e) => setName(e.target.value)} /></label>
          <Button type="submit" disabled={save.isPending || !name.trim() || tooLong}>Save this search</Button>
          {tooLong && <p className="sserr" role="alert">{TOO_LONG}</p>}
          {save.isError && <p className="sserr" role="alert">{saveError(save.error)}</p>}
        </form>
        {data?.length ? <ul className="sslist">{data.map((s) => <Row key={s.id} s={s} onOpen={() => openSaved(s)} />)}</ul> : <p className="ssempty">No saved searches yet.</p>}
      </PopoverContent>
    </Popover>
  );
}
