import { useReaderStore } from "@/features/reader/useReaderStore";
import { OPEN_PAPER, TEMPLATES } from "./quickPrompts";
import { useAskContext } from "./useAskContext";

interface Props { onSend: (q: string) => void; onFill: (template: string) => void }

/** One-click prompts: ask about the open paper, or start a question from a template. */
export function QuickPrompts({ onSend, onFill }: Props) {
  const { tabs } = useReaderStore();
  const { ignored } = useAskContext();
  const open = tabs.some((t) => !ignored.includes(t.docId));
  const items = open ? OPEN_PAPER : TEMPLATES;
  return (
    <div className="qp" role="group" aria-label={open ? "Ask about this paper" : "Question templates"}>
      {items.map((t) => (
        <button key={t} type="button" className="qchip" onClick={() => (open ? onSend(t) : onFill(t))}>{t}</button>
      ))}
    </div>
  );
}
