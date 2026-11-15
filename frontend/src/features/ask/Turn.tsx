import { memo, useRef, useState } from "react";
import { AnswerText } from "./AnswerText";
import { Sidenotes } from "./Sidenotes";
import { citeNumbers } from "./citeMarkers";
import { Button } from "@/components/ui/button";
import { AnswerFooter, type ChatCtx } from "./AnswerFooter";
import type { TurnData } from "./turns";

export const initials = (name: string | undefined) => {
  const w = (name ?? "").trim().split(/[\s._-]+/).filter(Boolean);
  return ((w.length > 1 ? w[0][0] + w[1][0] : (w[0] ?? "?").slice(0, 2)) || "?").toUpperCase();
};

interface Props { turn: TurnData; who: string; chat?: ChatCtx; onRetry: (question: string) => void }

function TurnImpl({ turn, who, chat, onRetry }: Props) {
  const { question, quote, answer } = turn;
  const ref = useRef<HTMLElement>(null);
  const [hot, setHot] = useState<number | null>(null);
  const streaming = answer?.status === "streaming";
  return (
    <div className="turn">
      <div className="q">
        <div className="avatar" aria-hidden>{who}</div>
        <div className="qt">
          {quote && <span className="quote">“{quote}”</span>}
          {question}
        </div>
      </div>
      {answer && (
        <article className="answer" ref={ref} aria-label="Answer" aria-busy={streaming}>
          <AnswerText text={answer.text} citations={answer.citations} streaming={streaming} hot={hot} onHot={setHot} />
          <Sidenotes citations={answer.citations} order={citeNumbers(answer.text)} excludes={chat?.excludes} answerRef={ref} hot={hot} onHot={setHot} />
          {(streaming || answer.status !== "complete") && <div className="ans-foot">
            {streaming && !answer.text && <p className="stat" role="status">Reading the library…</p>}
            {answer.status === "interrupted" && <p className="stat">Stopped before the answer finished.</p>}
            {answer.status === "failed" && (
              <div className="stat bad" role="alert">
                <span>The answer couldn't be completed. {answer.detail}</span>
                <Button onClick={() => onRetry(question)}>Try again</Button>
              </div>
            )}
          </div>}
          {chat && answer.status === "complete" && answer.id && <AnswerFooter question={question} answer={answer} chat={chat} />}
        </article>
      )}
    </div>
  );
}

export const Turn = memo(TurnImpl);
