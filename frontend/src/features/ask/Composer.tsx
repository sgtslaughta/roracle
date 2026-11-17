import { ArrowUp, Square } from "lucide-react";
import { useLayoutEffect, useRef, type FormEvent, type KeyboardEvent } from "react";
import type { ChatScope } from "@/api/chat";
import { Button } from "@/components/ui/button";
import { useMe } from "@/features/library/useMe";
import { ContextChips } from "./ContextChips";
import { loadHistory, pushHistory } from "./promptHistory";
import { ScopeSelect } from "./ScopeSelect";
import { registerComposer, setDraft, useDraft } from "./useAskContext";

export const MAX_Q = 4000;

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
  const area = useRef<HTMLTextAreaElement>(null);
  const uid = useMe().data?.id;
  /** recall position in the prompt history (null = not cycling) and the draft typed before cycling */
  const cursor = useRef<{ i: number; draft: string } | null>(null);

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
    if (uid) pushHistory(uid, q);
    cursor.current = null;
    setDraft("");
    onSend(q);
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.nativeEvent.isComposing) return;
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); return; }
    if ((e.key !== "ArrowUp" && e.key !== "ArrowDown") || e.shiftKey || e.ctrlKey || e.altKey || e.metaKey || !uid) return;
    const c = cursor.current, h = loadHistory(uid), up = e.key === "ArrowUp";
    const a = e.currentTarget;
    if (!c && !(up && h.length && ((a.selectionStart === 0 && a.selectionEnd === 0) || !draft.includes("\n")))) return;
    e.preventDefault();
    const i = (c?.i ?? h.length) + (up ? -1 : 1);
    if (i < 0) return;
    if (i >= h.length) { setDraft(c?.draft ?? ""); cursor.current = null; return; }
    cursor.current = { i, draft: c?.draft ?? draft };
    setDraft(h[i]);
  };
  const known = models.includes(model) ? models : [model, ...models].filter(Boolean);

  return (
    <form className="composer" onSubmit={submit}>
      <div className="cwrap">
        <ContextChips />
        <textarea
          ref={area} rows={1} aria-label="Question" maxLength={MAX_Q} value={draft} onChange={(e) => { cursor.current = null; setDraft(e.target.value); }} onKeyDown={onKey}
          placeholder="Ask about your documents, or about what you're reading"
        />
        <div className="crow">
          <ScopeSelect scope={scope} onScope={onScope} />
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
