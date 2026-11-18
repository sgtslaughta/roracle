import { useQueries } from "@tanstack/react-query";
import { Quote } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { apiFetch, seg } from "@/api/client";
import type { DocumentDetail, SearchHit } from "@/api/types";
import { SafeMarkdown } from "@/components/SafeMarkdown";
import { Button } from "@/components/ui/button";
import { docTitle } from "@/lib/docTitle";
import { citedDocIds, docCitation, withCiteTitles } from "./cite";
import { CitePicker } from "./CitePicker";
import { stripFrontMatter } from "./frontMatter";

/** Titles of the papers cited with [@doc:…] (shared cache with the reader's useDoc); unreadable ids are left out. */
function useCiteTitles(text: string, on: boolean): Record<string, string> {
  const ids = useMemo(() => (on ? citedDocIds(text) : []), [text, on]);
  const qs = useQueries({
    queries: ids.map((id) => ({ queryKey: ["doc", id], staleTime: 60_000, retry: false, queryFn: () => apiFetch<DocumentDetail>(`/documents/${seg(id)}`) })),
  });
  const out: Record<string, string> = {};
  qs.forEach((q, i) => { if (q.data) out[ids[i]] = docTitle(q.data); });
  return out;
}

/** Plain textarea + sanitized preview. Preview goes through SafeMarkdown only (no raw HTML, no images, safe links).
 *  "Insert citation" (or typing `[@`) searches the papers you can see and inserts `[@doc:<id>]` at the cursor. */
export function DraftEditor({ text, onChange, readOnly }: { text: string; onChange: (t: string) => void; readOnly: boolean }) {
  const [preview, setPreview] = useState(false);
  const [picking, setPicking] = useState<{ from: number; to: number } | null>(null);
  const area = useRef<HTMLTextAreaElement>(null);
  const titles = useCiteTitles(text, preview);

  const openPicker = (from?: number, to?: number) => {
    const el = area.current;
    setPicking({ from: from ?? el?.selectionStart ?? text.length, to: to ?? el?.selectionEnd ?? text.length });
  };
  const restore = (caret: number) => requestAnimationFrame(() => { const el = area.current; if (el) { el.focus(); el.setSelectionRange(caret, caret); } });
  const insert = (hit: SearchHit) => {
    if (!picking) return;
    const cite = docCitation(hit.doc_id);
    onChange(text.slice(0, picking.from) + cite + text.slice(picking.to));
    setPicking(null);
    restore(picking.from + cite.length);
  };
  const close = () => { if (picking) restore(picking.to); setPicking(null); };
  const typed = (next: string, caret: number) => {
    onChange(next);
    // `[@` just typed: offer the picker, which replaces those two characters
    if (next.length === text.length + 1 && next.slice(caret - 2, caret) === "[@") openPicker(caret - 2, caret);
  };

  return (
    <div className="deditor">
      <div className="dtools">
        <div className="seg" role="group" aria-label="Editor mode">
          <button aria-pressed={!preview} onClick={() => setPreview(false)}>Edit</button>
          <button aria-pressed={preview} onClick={() => setPreview(true)}>Preview</button>
        </div>
        {!readOnly && !preview && <Button variant="ghost" onClick={() => openPicker()} title="Insert citation (or type [@)"><Quote aria-hidden />Insert citation</Button>}
      </div>
      {preview
        ? <div className="dpreview" role="region" aria-label="Preview"><SafeMarkdown text={withCiteTitles(stripFrontMatter(text), titles)} /></div>
        : <textarea ref={area} className="inp dtext" aria-label="Draft content" spellCheck value={text} readOnly={readOnly} onChange={(e) => typed(e.target.value, e.target.selectionStart)} />}
      <CitePicker open={!!picking} onClose={close} onPick={insert} />
    </div>
  );
}
