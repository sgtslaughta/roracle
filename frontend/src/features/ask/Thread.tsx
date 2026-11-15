import { ArrowDown, MessagesSquare } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import type { ChatCtx } from "./AnswerFooter";
import { Turn } from "./Turn";
import type { TurnData } from "./turns";

const NEAR = 80;

export function Thread({ turns, who, chat, onRetry, scrollKey }: { turns: TurnData[]; who: string; chat?: ChatCtx; onRetry: (q: string) => void; scrollKey: unknown }) {
  const box = useRef<HTMLDivElement>(null);
  const near = useRef(true);
  const [away, setAway] = useState(false);
  const last = turns.at(-1);

  const toBottom = () => { const el = box.current; if (el) el.scrollTop = el.scrollHeight; near.current = true; setAway(false); };
  const onScroll = () => {
    const el = box.current;
    if (!el) return;
    near.current = el.scrollHeight - el.scrollTop - el.clientHeight <= NEAR;
    if (near.current) setAway(false);
  };

  // a new question always scrolls to it; streamed text follows only while the reader is at the bottom
  useLayoutEffect(toBottom, [turns.length]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (last?.answer?.status !== "streaming") return;
    if (near.current) toBottom(); else setAway(true);
  }, [scrollKey, last?.answer?.text]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="thread-wrap">
      <div className="thread" ref={box} onScroll={onScroll}>
        {turns.length ? (
          <div role="log" aria-label="Conversation">
            {turns.map((t) => <Turn key={t.key} turn={t} who={who} chat={chat} onRetry={onRetry} />)}
          </div>
        ) : (
          <div className="turn">
            <div className="empty">
              <MessagesSquare aria-hidden />
              <p>Ask a question about the library. Documents open in the reader count as context, and every answer cites the passages it used.</p>
            </div>
          </div>
        )}
      </div>
      {away && <Button className="jump" variant="primary" onClick={toBottom}><ArrowDown aria-hidden />Jump to latest</Button>}
    </div>
  );
}
