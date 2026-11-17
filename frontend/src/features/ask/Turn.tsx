import { memo, useMemo, useRef, useState } from "react";
import { AnswerText } from "./AnswerText";
import { Sidenotes } from "./Sidenotes";
import { citeNumbers } from "./citeMarkers";
import { Button } from "@/components/ui/button";
import { ExcludedNote } from "./ExcludedNote";
import { AnswerFooter, type ChatCtx } from "./AnswerFooter";
import type { TurnData } from "./turns";

interface Props { turn: TurnData; who: string; chat?: ChatCtx; onRetry: (question: string) => void }

function TurnImpl({ turn, who, chat, onRetry }: Props) {
  const { question, quote, answer } = turn;
  const ref = useRef<HTMLElement>(null);
  const [hot, setHot] = useState<number | null>(null);
  const streaming = answer?.status === "streaming";
  const order = useMemo(() => citeNumbers(answer?.text ?? ""), [answer?.text]);
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
          {!!answer.excluded?.length && <ExcludedNote items={answer.excluded} />}
          <Sidenotes citations={answer.citations} order={order} excludes={chat?.excludes} answerRef={ref} hot={hot} onHot={setHot} />
          {(streaming || answer.status !== "complete") && <div className="ans-foot">
            {streaming && !answer.text && <p className="stat" role="status">{answer.progress ?? "Reading the library…"}</p>}
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
