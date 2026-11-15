import { ArrowUp, Square } from "lucide-react";
import { useLayoutEffect, useRef, type FormEvent, type KeyboardEvent } from "react";
import type { ChatScope } from "@/api/chat";
import { Button } from "@/components/ui/button";
import { useCollections } from "@/features/library/api";
import { useReaderStore } from "@/features/reader/useReaderStore";
import { ContextChips } from "./ContextChips";
import { registerComposer, setDraft, useDraft } from "./useAskContext";

interface Props {
  scope: ChatScope;
  onScope: (s: ChatScope) => void;
  model: string;
  models: string[];
  onModel: (m: string) => void;
  streaming: boolean;
  /** a chat is being created; sending is paused */
  busy?: boolean;
  onSend: (question: string) => void;
  onStop: () => void;
}

export function Composer({ scope, onScope, model, models, onModel, streaming, busy, onSend, onStop }: Props) {
  const draft = useDraft();
  const { tabs } = useReaderStore();
  const collections = useCollections().data ?? [];
  const area = useRef<HTMLTextAreaElement>(null);

  useLayoutEffect(() => { registerComposer(area.current); return () => registerComposer(null); }, []);
  useLayoutEffect(() => {
    const a = area.current;
    if (!a) return;
    a.style.height = "auto";
    a.style.height = `${Math.min(a.scrollHeight, 180)}px`;
  }, [draft]);

  const submit = (e?: FormEvent) => {
    e?.preventDefault();
    const q = draft.trim();
    if (!q || streaming || busy) return;
    setDraft("");
    onSend(q);
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit(); }
  };
  const known = models.includes(model) ? models : [model, ...models].filter(Boolean);

  return (
    <form className="composer" onSubmit={submit}>
      <div className="cwrap">
        <ContextChips />
        <textarea
          ref={area} rows={1} aria-label="Question" value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={onKey}
          placeholder="Ask about your documents, or about what you're reading"
        />
        <div className="crow">
          <select className="sel" aria-label="Search in" value={scope} onChange={(e) => onScope(e.target.value as ChatScope)}>
            <option value="all">All documents you can see</option>
            <option value="open" disabled={!tabs.length}>Open documents only</option>
            {collections.map((c) => <option key={c.id} value={`collection:${c.id}`}>{`Collection: ${c.name}`}</option>)}
          </select>
          <select className="sel" aria-label="Model" value={model} onChange={(e) => onModel(e.target.value)}>
            {known.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
          {streaming ? (
            <Button variant="primary" className="send" onClick={onStop}><Square aria-hidden />Stop</Button>
          ) : (
            <Button type="submit" variant="primary" className="send" disabled={!draft.trim() || busy}><ArrowUp aria-hidden />Ask</Button>
          )}
        </div>
      </div>
    </form>
  );
}
