import { useEffect, useRef, type RefObject } from "react";
import { OverflowList } from "@/components/OverflowList";
import { isAvailable, type Citation } from "@/api/chat";
import { openCitation } from "./AnswerText";

const CAP = 12;
const GONE_TITLE = "You can't see this source, or it was removed.";

export function trimQuote(q: string, max = 160) {
  if (q.length <= max) return q;
  const cut = q.slice(0, max);
  return (q[max] && /\s/.test(q[max]) ? cut : cut.replace(/\s\S*$/, "")) + "…";
}

export interface SidenotesProps {
  citations: Citation[];
  order: number[];
  answerRef: RefObject<HTMLElement | null>;
  hot?: number | null;
  onHot?: (n: number | null) => void;
}

export function Sidenotes({ citations, order, answerRef, hot, onHot }: SidenotesProps) {
  const marginRef = useRef<HTMLDivElement>(null);
  const list = order.flatMap((n) => citations.find((c) => c.n === n) ?? []);

  useEffect(() => {
    const answer = answerRef.current, margin = marginRef.current;
    if (!answer || !margin) return;
    const layout = () => {
      const notes = [...margin.querySelectorAll<HTMLElement>(".note")];
      if (!notes.length || getComputedStyle(notes[0]).position !== "absolute") return;
      const top0 = answer.getBoundingClientRect().top;
      let floor = 0;
      for (const note of notes) {
        const mark = answer.querySelector(`.ans-text [data-n="${note.dataset.n}"]`);
        const t = Math.max(mark ? mark.getBoundingClientRect().top - top0 - 8 : floor, floor);
        note.style.top = `${t}px`;
        floor = t + note.offsetHeight + 8;
      }
      const more = margin.querySelector<HTMLElement>(".ovl-more");
      if (more) { more.style.top = `${floor}px`; floor += more.offsetHeight + 8; }
      margin.style.minHeight = `${floor}px`;
    };
    layout();
    const ro = new ResizeObserver(layout);
    ro.observe(answer);
    ro.observe(margin);
    window.addEventListener("resize", layout);
    return () => { ro.disconnect(); window.removeEventListener("resize", layout); };
  }, [answerRef, citations, order]);

  if (!list.length) return <div className="margin" ref={marginRef} />;
  return (
    <div className="margin" ref={marginRef}>
      <OverflowList
        className="ovl-flat" items={list} max={CAP} layout="stack" menuLabel="More sources"
        moreLabel={(k) => `+${k} more sources`}
        renderItem={(c) => {
          if (!isAvailable(c)) {
            return (
              <div key={c.n} className="note gone" data-n={c.n} title={GONE_TITLE}>
                <div className="nh"><span className="nn">{c.n}</span><span className="nm">Source not available</span></div>
              </div>
            );
          }
          return (
            <button
              key={c.n} type="button" className={`note${hot === c.n ? " hot" : ""}`} data-n={c.n}
              onClick={() => openCitation(c)}
              onMouseEnter={() => onHot?.(c.n)} onMouseLeave={() => onHot?.(null)}
              onFocus={() => onHot?.(c.n)} onBlur={() => onHot?.(null)}
            >
              <span className="nh">
                <span className="nn">{c.n}</span>
                <span className="nt" title={c.title}>{c.title}</span>
                <span className="nm">p. {c.page}</span>
              </span>
              <span className="crop">{trimQuote(c.quote)}</span>
            </button>
          );
        }}
      />
    </div>
  );
}
