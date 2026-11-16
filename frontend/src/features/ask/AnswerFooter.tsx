import { Copy, Layers } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { answerMarkdown } from "./answerMarkdown";
import { PassagesDrawer } from "./PassagesDrawer";
import { RateControl } from "./RateControl";
import type { AnswerData } from "./turns";

export interface ChatCtx { id: string; owner: boolean; pins: string[]; excludes: string[] }

/** Passages, copy and rating under a finished answer. */
export function AnswerFooter({ question, answer, chat }: { question: string; answer: AnswerData; chat: ChatCtx }) {
  const [open, setOpen] = useState(false);
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
