import { EyeOff, Pin } from "lucide-react";
import { useState } from "react";
import type { RetrievedPassage } from "@/api/chat";
import { Button } from "@/components/ui/button";
import { openDoc } from "@/features/reader/useReaderStore";
import { usePatchChat } from "./api";

const CAP = 20;
const clip = (s: string, n: number) => (s.length > n ? `${s.slice(0, n).trimEnd()}…` : s);
const toggle = (list: string[], id: string) => (list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);

interface Props { id: string; chatId: string; retrieved: RetrievedPassage[]; pins: string[]; excludes: string[]; owner: boolean }

/** The passages the model read, best first. Owners can pin a document into, or leave it out of, later answers. */
export function PassagesDrawer({ id, chatId, retrieved, pins, excludes, owner }: Props) {
  const patch = usePatchChat(chatId);
  const [all, setAll] = useState(false);
  const rows = [...retrieved].sort((a, b) => b.score - a.score);
  const shown = all ? rows : rows.slice(0, CAP);

  const pin = (doc: string) => {
    const on = pins.includes(doc);
    patch.mutate({ pins: toggle(pins, doc), excludes: on ? excludes : excludes.filter((x) => x !== doc) });
  };
  const exclude = (doc: string) => {
    const on = excludes.includes(doc);
    patch.mutate({ excludes: toggle(excludes, doc), pins: on ? pins : pins.filter((x) => x !== doc) });
  };

  return (
    <div className="drawer" id={id}>
      <ol aria-label="Passages read">
        {shown.map((p) => {
          const pinned = pins.includes(p.doc_id), left = excludes.includes(p.doc_id);
          return (
            <li key={`${p.chunk_id}-${p.n ?? ""}`}>
              <div>
                <button type="button" className="ps" onClick={() => openDoc(p.doc_id, { page: p.page, chunkId: p.chunk_id })}>{clip(p.text, 220)}</button>
                <div className="pm">{p.title}, page {p.page}. Relevance {p.score.toFixed(2)}{pinned ? ". Pinned" : left ? ". Left out of next answers" : ""}</div>
              </div>
              {owner && (
                <div className="acts">
                  <Button variant="ghost" aria-pressed={pinned} onClick={() => pin(p.doc_id)} title="Always include this document in later answers"><Pin aria-hidden />Pin</Button>
                  <Button variant="ghost" aria-pressed={left} onClick={() => exclude(p.doc_id)} title="Leave this document out of later answers"><EyeOff aria-hidden />Exclude</Button>
                </div>
              )}
            </li>
          );
        })}
      </ol>
      {rows.length > CAP && !all && <Button variant="ghost" className="drawer-all" onClick={() => setAll(true)}>Show all {rows.length}</Button>}
    </div>
  );
}
