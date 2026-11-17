import { useState } from "react";
import { Search, SlidersHorizontal } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { SCOPES, type LibrarySearch } from "@/lib/searchParams";
import { joinQuery, splitQuery } from "./queryBuilder";
import type { useLibraryNav } from "./useLibraryNav";

const SCOPE_LABEL = { anywhere: "Anywhere", title: "Title", authors: "Authors", abstract: "Abstract", fulltext: "Full text" } as const;
const lines = (s: string) => s.split("\n").map((x) => x.trim()).filter(Boolean);

export function Refine({ q, onApply }: { q: string; onApply: (q: string) => void }) {
  const init = splitQuery(q);
  const [words, setWords] = useState(init.words);
  const [phrases, setPhrases] = useState(init.phrases.join("\n"));
  const [excluded, setExcluded] = useState(init.excluded.join("\n"));
  const built = joinQuery({ words, phrases: lines(phrases), excluded: lines(excluded) });
  const noPositive = !joinQuery({ words, phrases: lines(phrases), excluded: [] });
  return (
    <form className="refine" onSubmit={(e) => { e.preventDefault(); if (!noPositive) onApply(built); }}>
      <label>All of these words<input className="sel" value={words} onChange={(e) => setWords(e.target.value)} /></label>
      <label>This exact phrase<textarea className="sel" rows={2} value={phrases} onChange={(e) => setPhrases(e.target.value)} placeholder="One phrase per line" /></label>
      <label>None of these words<textarea className="sel" rows={2} value={excluded} onChange={(e) => setExcluded(e.target.value)} placeholder="One word or phrase per line" /></label>
      {built && noPositive && <p className="refine-err" role="alert">Add at least one word to search for</p>}
      <Button type="submit" variant="primary" disabled={noPositive}>Apply</Button>
    </form>
  );
}

export function ScopeSelect({ search, nav }: { search: LibrarySearch; nav: Pick<ReturnType<typeof useLibraryNav>, "patch"> }) {
  return (
    <select className="sel" aria-label="Search in" value={search.in ?? "anywhere"} onChange={(e) => nav.patch({ in: e.target.value as LibrarySearch["in"] })}>
      {SCOPES.map((s) => <option key={s} value={s}>{SCOPE_LABEL[s]}</option>)}
    </select>
  );
}

export function QueryBar({ search, nav }: { search: LibrarySearch; nav: Pick<ReturnType<typeof useLibraryNav>, "patch"> }) {
  const q = search.q ?? "";
  const [draft, setDraft] = useState(q);
  const [synced, setSynced] = useState(q);
  const [open, setOpen] = useState(false);
  if (synced !== q) { setSynced(q); setDraft(q); }
  return (
    <div className="qbar">
    <form className="qbar-form" role="search" onSubmit={(e) => { e.preventDefault(); nav.patch({ q: draft.trim() || undefined }); }}>
      <span className="qbar-input"><Search aria-hidden />
        <input type="search" aria-label="Search the library" value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Search the library" />
      </span>
      <ScopeSelect search={search} nav={nav} />
    </form>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild><Button><SlidersHorizontal aria-hidden />Refine</Button></PopoverTrigger>
        <PopoverContent className="refine-pop" align="end" aria-label="Refine the query">
          <Refine q={q} onApply={(v) => { nav.patch({ q: v }); setOpen(false); }} />
        </PopoverContent>
      </Popover>
    </div>
  );
}
