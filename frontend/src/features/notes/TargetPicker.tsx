import { useQuery } from "@tanstack/react-query";
import { FileText, X } from "lucide-react";
import { useId, useState } from "react";
import { apiFetch } from "@/api/client";
import type { SearchResponse } from "@/api/types";

export interface Target { id: string; title: string }

/** Pick the related document from the library (GET /search?q=). Titles are plain text. */
export function TargetPicker({ docId, value, onChange }: { docId: string; value: Target | null; onChange: (t: Target | null) => void }) {
  const id = useId();
  const [q, setQ] = useState("");
  const term = q.trim();
  const res = useQuery({
    queryKey: ["note-target", term],
    enabled: term.length >= 2 && !value,
    queryFn: () => apiFetch<SearchResponse>(`/search?${new URLSearchParams({ q: term, size: "8" })}`),
  });
  if (value) {
    return (
      <div className="ntarget">
        <span className="lbl">Related document</span>
        <span className="cchip" title={value.title}>
          <FileText aria-hidden /><span className="cchip-t">{value.title || "Untitled document"}</span>
          <button type="button" aria-label="Change related document" onClick={() => onChange(null)}><X aria-hidden /></button>
        </span>
      </div>
    );
  }
  const hits = res.data?.results.filter((h) => h.doc_id !== docId) ?? [];
  return (
    <div className="ntarget">
      <label htmlFor={id} className="lbl">Related document</label>
      <input id={id} type="search" value={q} placeholder="Search the library by title or topic" onChange={(e) => setQ(e.target.value)} />
      {term.length >= 2 && (
        <ul className="ntarget-list" aria-label="Matching documents">
          {res.isLoading && <li className="sub">Searching…</li>}
          {res.data && !hits.length && <li className="sub">Nothing you can see matches.</li>}
          {hits.map((h) => (
            <li key={h.doc_id}>
              <button type="button" className="hdoc" title={h.title} onClick={() => onChange({ id: h.doc_id, title: h.title })}>
                <FileText aria-hidden /><span className="trunc">{h.title || "Untitled document"}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
