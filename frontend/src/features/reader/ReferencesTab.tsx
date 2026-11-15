import { ArrowUpRight, CornerDownRight, Globe, Link2 } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";

const PAGE = 25;
function MoreButton({ shown, total, onMore }: { shown: number; total: number; onMore: () => void }) {
  return shown < total ? <li className="more-row"><Button onClick={onMore}>Show {Math.min(PAGE, total - shown)} more ({total - shown} left)</Button></li> : null;
}
import { shortCite } from "@/lib/cite";
import { useCitedBy, useReferences } from "./api";
import { openDoc } from "./useReaderStore";

export function ReferencesTab({ docId }: { docId: string }) {
  const { data, isLoading } = useReferences(docId);
  const [n, setN] = useState(PAGE);
  return (
    <div className="refs">
      <p className="refnote">References found in the library link straight to the document.</p>
      <ol>
        {isLoading && <li className="empty">Loading…</li>}
        {data && !data.length && <li className="empty">This document lists no references.</li>}
        {data?.slice(0, n).map((r) => (
          <li key={r.ref_no} className={r.target ? "ref" : "ref ext"}>
            <span className="rn">{r.ref_no}</span>
            <div>
              <div className="rt">{r.raw}</div>
              <div className="rs">
                {r.target
                  ? <span className="inlib"><Link2 aria-hidden />In the library</span>
                  : <span className="badge-o"><Globe aria-hidden />Not in the library</span>}
              </div>
            </div>
            {r.target ? <Button onClick={() => openDoc(r.target!.doc_id)}>Open<ArrowUpRight aria-hidden /></Button> : <span />}
          </li>
        ))}
        {data && <MoreButton shown={n} total={data.length} onMore={() => setN(n + PAGE)} />}
      </ol>
    </div>
  );
}

export function CitedByTab({ docId }: { docId: string }) {
  const { data, isLoading } = useCitedBy(docId);
  const [n, setN] = useState(PAGE);
  return (
    <div className="refs">
      <p className="refnote">Documents in the library that cite this one.</p>
      <ol>
        {isLoading && <li className="empty">Loading…</li>}
        {data && !data.length && <li className="empty">Nothing in the library cites this yet.</li>}
        {data?.slice(0, n).map((c) => (
          <li key={c.doc_id} className="ref">
            <span className="rn"><CornerDownRight aria-hidden /></span>
            <div>
              <div className="rt">{c.authors.join(", ")}. {c.title}. {c.venue && <i>{c.venue}</i>}{c.venue && ", "}{c.year}.</div>
              <div className="rs"><span className="vis">{shortCite(c.authors, c.year)}</span></div>
            </div>
            <Button onClick={() => openDoc(c.doc_id)}>Open<ArrowUpRight aria-hidden /></Button>
          </li>
        ))}
        {data && <MoreButton shown={n} total={data.length} onMore={() => setN(n + PAGE)} />}
      </ol>
    </div>
  );
}

export function NotesTab() {
  return (
    <div className="refs">
      <p className="refnote">Notes from readers and their agents. Agent notes appear only after their user approves them.</p>
      <div className="empty"><p>No notes yet. Notes arrive in the next release.</p></div>
    </div>
  );
}
