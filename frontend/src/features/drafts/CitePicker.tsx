import { useQuery } from "@tanstack/react-query";
import { FileText } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { apiFetch } from "@/api/client";
import type { SearchHit, SearchResponse } from "@/api/types";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { shortCite } from "@/lib/cite";
import { stringifySearch } from "@/lib/searchParams";
import { MIN_SEARCH_CHARS, SEARCH_DEBOUNCE_MS } from "@/lib/searchTyping";
import { useDebounced } from "@/lib/useDebounced";

interface Props { open: boolean; onClose: () => void; onPick: (hit: SearchHit) => void }

/** Search the papers you can see by title (the palette's quick search) and pick one to cite. */
export function CitePicker({ open, onClose, onPick }: Props) {
  const id = useId();
  const [q, setQ] = useState("");
  useEffect(() => { if (open) setQ(""); }, [open]);
  const debounced = useDebounced(q.trim(), SEARCH_DEBOUNCE_MS);
  const found = useQuery({
    queryKey: ["cite-search", debounced], enabled: open && debounced.length >= MIN_SEARCH_CHARS, staleTime: 30_000,
    queryFn: () => apiFetch<SearchResponse>(`/search${stringifySearch({ q: debounced }, { mode: "quick" })}`),
  });
  const hits = found.data?.results ?? [];
  const ready = debounced.length >= MIN_SEARCH_CHARS;
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent onCloseAutoFocus={(e) => e.preventDefault()}>
        <DialogTitle className="text-base font-semibold">Insert citation</DialogTitle>
        <DialogDescription className="sub">Search the papers you can see by title. The citation goes in at the cursor as [@doc:…].</DialogDescription>
        <label htmlFor={id} className="sr-only">Paper title</label>
        <input id={id} className="inp" autoFocus autoComplete="off" placeholder={`Type at least ${MIN_SEARCH_CHARS} letters of the title`} value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && hits[0]) { e.preventDefault(); onPick(hits[0]); } }} />
        {ready && (
          <ul className="dirlist citelist" aria-label="Matching papers">
            {hits.map((h) => (
              <li key={h.doc_id}>
                <button type="button" onClick={() => onPick(h)}>
                  <FileText aria-hidden /><span className="citet">{h.title || "Untitled document"}</span>
                  {shortCite(h.authors, h.year) && <small className="sub">{shortCite(h.authors, h.year)}</small>}
                </button>
              </li>
            ))}
            {!hits.length && !found.isFetching && <li className="sub">{found.isError ? "Search is unavailable right now." : "No papers match."}</li>}
          </ul>
        )}
      </DialogContent>
    </Dialog>
  );
}
