import { Bot, Copy, Layers } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { ContinueInAgent } from "@/features/agents/ContinueInAgent";
import type { HandoffSource } from "@/features/agents/HandoffDialog";
import { answerMarkdown } from "./answerMarkdown";
import { PassagesDrawer } from "./PassagesDrawer";
import { RateControl } from "./RateControl";
import type { AnswerData } from "./turns";

const BRIEF_ANSWER = 1500;

/** Chat handoff prefill: the backend adds the full transcript and cited passages; the brief is the human summary. */
export function chatHandoff(chatId: string, question: string, answer: string): HandoffSource {
  const found = answer.length > BRIEF_ANSWER ? `${answer.slice(0, BRIEF_ANSWER)}…` : answer;
  return {
    kind: "chat", sourceId: chatId, docIds: [], title: "", titlePlaceholder: "Defaults to the chat title",
    goal: question.slice(0, 2000), brief: `Goal: ${question}\n\nFound so far:\n${found}\n\nOpen:\n`,
    summary: "Hands this chat to an agent so it starts with your context: the transcript and the cited passages you can see.",
  };
}

export interface ChatCtx { id: string; owner: boolean; pins: string[]; excludes: string[] }

/** Passages, copy and rating under a finished answer. */
export function AnswerFooter({ question, answer, chat }: { question: string; answer: AnswerData; chat: ChatCtx }) {
  const [open, setOpen] = useState(false);
  const [handedOff, setHandedOff] = useState<string | null>(null);
  const retrieved = answer.retrieved;
  const drawerId = `drawer-${answer.id}`;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(answerMarkdown(question, answer.text, answer.citations));
      toast("Answer copied as Markdown");
    } catch {
      toast("Copying isn't available here. Use Export in the chat menu instead.");
    }
  };

  return (
    <>
      <div className="ans-foot">
        {handedOff && <span className="hchip"><Bot aria-hidden />Handed off as {handedOff}</span>}
        {chat.owner && <ContinueInAgent variant="ghost" source={() => chatHandoff(chat.id, question, answer.text)} onCreated={(m) => setHandedOff(m.display)} />}
        {retrieved.length > 0 && (
          <Button variant="ghost" aria-expanded={open} aria-controls={drawerId} onClick={() => setOpen(!open)}>
            <Layers aria-hidden />{open ? "Hide" : "Show"} the {retrieved.length} passages read
          </Button>
        )}
        <Button variant="ghost" onClick={() => void copy()}><Copy aria-hidden />Copy as Markdown</Button>
        {chat.owner && answer.id && <RateControl chatId={chat.id} messageId={answer.id} rating={answer.rating} />}
      </div>
      {open && retrieved.length > 0 && <PassagesDrawer id={drawerId} chatId={chat.id} retrieved={retrieved} pins={chat.pins} excludes={chat.excludes} owner={chat.owner} />}
    </>
  );
}
