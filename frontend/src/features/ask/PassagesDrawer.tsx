import { EyeOff, Pin, TextQuote } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { ChatDetail, RetrievedPassage } from "@/api/chat";
import { Button } from "@/components/ui/button";
import { openDoc } from "@/features/reader/useReaderStore";
import { usePatchChat } from "./api";
import { focusComposer, setQuote } from "./useAskContext";

const CAP = 20;
const clip = (s: string, n: number) => (s.length > n ? `${s.slice(0, n).trimEnd()}…` : s);
const toggle = (list: string[], id: string) => (list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);

interface Props { id: string; chatId: string; retrieved: RetrievedPassage[]; pins: string[]; excludes: string[]; owner: boolean }

/** The passages the model read, best first. Owners can pin a document into, or leave it out of, later answers. */
export function PassagesDrawer({ id, chatId, retrieved, pins, excludes, owner }: Props) {
  const qc = useQueryClient();
  const patch = usePatchChat(chatId);
  const [all, setAll] = useState(false);
  const rows = [...retrieved].sort((a, b) => b.score - a.score);
  const shown = all ? rows : rows.slice(0, CAP);

  // derive from the latest cache, so quick successive clicks build on each other
  const latest = () => {
    const c = qc.getQueryData<ChatDetail>(["chat", chatId]);
    return { pins: c?.pins ?? pins, excludes: c?.excludes ?? excludes };
  };
  const pin = (doc: string) => {
    const l = latest(), on = l.pins.includes(doc);
    patch.mutate({ pins: toggle(l.pins, doc), excludes: on ? l.excludes : l.excludes.filter((x) => x !== doc) });
  };
  const exclude = (doc: string) => {
    const l = latest(), on = l.excludes.includes(doc);
    patch.mutate({ excludes: toggle(l.excludes, doc), pins: on ? l.pins : l.pins.filter((x) => x !== doc) });
  };

  return (
    <div className="drawer" id={id}>
      <ol aria-label="Passages read">
        {shown.map((p) => {
          const pinned = pins.includes(p.doc_id), left = excludes.includes(p.doc_id);
          return (
            <li key={`${p.chunk_id}-${p.n ?? p.note_id}`}>
              <div>
                <button type="button" className="ps" onClick={() => openDoc(p.doc_id, { page: p.page, chunkId: p.chunk_id ?? undefined, version: p.version ?? undefined, bbox: p.bbox })}>{clip(p.text, 220)}</button>
                <div className="pm">{p.n == null && "Community note. "}{p.title}, page {p.page}. Relevance {p.score.toFixed(2)}{pinned ? ". Pinned" : left ? ". Left out of next answers" : ""}</div>
              </div>
              {owner && (
                <div className="acts">
                  <Button variant="ghost" onClick={() => { setQuote({ doc_id: p.doc_id, page: p.page, text: p.text }); focusComposer(); }} title="Ask the next question about this passage"><TextQuote aria-hidden />Quote</Button>
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
