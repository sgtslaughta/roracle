import { Fragment, useEffect, useRef, useState, type KeyboardEvent } from "react";
import { X } from "lucide-react";
import { ApiError } from "@/api/client";
import type { FacetName, FacetValue } from "@/api/types";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useLabelName } from "@/features/labels/api";
import { useDoc } from "@/features/reader/api";
import type { LibrarySearch } from "@/lib/searchParams";
import { useCollectionName } from "./api";
import { ClauseEditor } from "./ClauseEditor";
import { buildSentence, type Clause, type Names } from "./querySentence";
import type { useLibraryNav } from "./useLibraryNav";

type Nav = Pick<ReturnType<typeof useLibraryNav>, "toggle" | "patch" | "setSort">;
const LEAVE_MS = 160;
const INPUT = "input[aria-label='Search the library']";

/** Collection, label and cited-paper names for the sentence. A paper still loading reads as "…". */
export function useSentenceNames(cites?: string, facets?: Record<FacetName, FacetValue[]>): Names {
  const collection = useCollectionName();
  const label = useLabelName();
  const doc = useDoc(cites ?? "", !!cites);
  const gone = doc.error instanceof ApiError && doc.error.status === 404;
  const journal = (issn: string) => { const l = facets?.journal?.find((f) => f.value === issn)?.label; return typeof l === "string" ? l : issn; };
  return { collection, label, journal, doc: () => (doc.data ? doc.data.title || "Untitled" : gone ? undefined : "…") };
}

const animate = () => window.matchMedia?.("(prefers-reduced-motion: no-preference)").matches === true;

/** The current search read as a sentence; each clause is a chip you can edit in place or remove. */
export function QuerySentence({ search, nav, names, facets }: { search: LibrarySearch; nav: Nav; names: Names; facets?: Record<FacetName, FacetValue[]> }) {
  const clauses = buildSentence(search, names);
  const [open, setOpen] = useState<string | null>(null);
  const [leaving, setLeaving] = useState<string[]>([]);
  const chips = useRef(new Map<string, HTMLButtonElement>());
  const pending = useRef<string | null>(null); // clause id to focus after the URL updates, or INPUT
  const ids = clauses.map((c) => c.id).join();

  useEffect(() => {
    if (open && !clauses.some((c) => c.id === open)) { setOpen(null); pending.current ??= INPUT; } // its editor removed the clause
    const want = pending.current;
    if (!want) return;
    const el = want === INPUT ? document.querySelector<HTMLElement>(INPUT) : chips.current.get(want);
    if (el) { el.focus(); pending.current = null; }
  }, [ids]); // eslint-disable-line react-hooks/exhaustive-deps

  const go = (patch: Partial<LibrarySearch>, gone: string[], focus: string) => {
    pending.current = focus;
    if (!animate()) return nav.patch(patch);
    setLeaving(gone);
    window.setTimeout(() => { setLeaving([]); nav.patch(patch); }, LEAVE_MS);
  };
  const remove = (c: Clause, i: number) => go(c.remove, [c.id], clauses[i + 1]?.id ?? INPUT);
  const clearAll = () => go(Object.assign({}, ...clauses.map((c) => c.remove)), clauses.map((c) => c.id), INPUT);
  const onKey = (c: Clause, i: number) => (e: KeyboardEvent) => {
    if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); remove(c, i); }
  };

  if (!clauses.length) return <p className="qsentence" aria-label="Current search"><span className="qlead">All papers you can see</span></p>;
  return (
    <p className="qsentence" aria-label="Current search">
      <span className="qlead">Papers</span>{" "}
      {/* the separator is glued to the chip before it (no-break space), so no line starts with "·" */}
      {clauses.map((c, i) => (
        <Fragment key={c.id}>
          <span className="qchip" data-leaving={leaving.includes(c.id) || undefined}>
            <Popover open={open === c.id} onOpenChange={(o) => setOpen(o ? c.id : null)}>
              <PopoverTrigger asChild>
                <button
                  type="button" className="qchip-t" aria-haspopup="dialog" onKeyDown={onKey(c, i)}
                  ref={(el) => { if (el) chips.current.set(c.id, el); else chips.current.delete(c.id); }}
                >{c.text}</button>
              </PopoverTrigger>
              <PopoverContent className="cedit" align="start" sideOffset={8} aria-label={`Edit: ${c.text}`}>
                <p className="cedit-cap">Papers <em>{c.text}</em></p>
                <ClauseEditor edit={c.edit} search={search} nav={nav} facets={facets} onDone={() => setOpen(null)} />
              </PopoverContent>
            </Popover>
            <button type="button" className="qchip-x" aria-label={`Remove: ${c.text}`} onClick={() => remove(c, i)} onKeyDown={onKey(c, i)}><X aria-hidden /></button>
          </span>
          {i < clauses.length - 1 && <><span className="qsep" aria-hidden>{"\u00a0·"}</span>{" "}</>}
        </Fragment>
      ))}
      {clauses.length >= 2 && <>{" "}<button type="button" className="qclear" onClick={clearAll}>Clear all</button></>}
    </p>
  );
}
