import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { MessageCircleOff } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import type { ChatScope } from "@/api/chat";
import { Button } from "@/components/ui/button";
import { useMe } from "@/features/library/useMe";
import { attachChat, startTurn, stopTurn, usePendingTurn } from "./askStore";
import { useChat, useCreateChat, useModels, usePatchChat } from "./api";
import { Composer } from "./Composer";
import { Thread } from "./Thread";
import { initials } from "./Turn";
import { buildTurns } from "./turns";
import { focusComposer, getAskContext, setDraft, setQuote } from "./useAskContext";
import { useViewer } from "./viewer";

/** First 80 characters of the question, cut at a word boundary. */
export function titleOf(q: string) {
  const s = q.trim().replace(/\s+/g, " ");
  if (s.length <= 80) return s;
  return (/\s/.test(s[80]) ? s.slice(0, 80) : s.slice(0, 80).replace(/\s+\S*$/, "")) || s.slice(0, 80);
}

export function AskPage({ chatId }: { chatId?: string }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const getViewer = useViewer();
  const chatQ = useChat(chatId);
  const chat = chatQ.data;
  const models = useModels().data;
  const me = useMe().data;
  const pending = usePendingTurn(chatId);
  const create = useCreateChat();
  const patch = usePatchChat(chatId ?? "");
  const [newScope, setNewScope] = useState<ChatScope>("all");
  const [pickedModel, setModel] = useState<string>();

  useEffect(() => (chatId ? attachChat(chatId) : undefined), [chatId]);

  const turns = useMemo(() => buildTurns(chat?.messages ?? [], pending), [chat?.messages, pending]);
  const streaming = pending?.status === "streaming";
  const scope = chat?.scope ?? newScope;
  const model = pickedModel ?? chat?.model ?? models?.default ?? "";

  const send = async (question: string) => {
    const { ignored, quote } = getAskContext();
    const viewer = getViewer(ignored, quote);
    setQuote(null);
    let id = chatId;
    if (!id) {
      try {
        id = (await create.mutateAsync({ title: titleOf(question), scope: newScope, model: model || undefined })).id;
      } catch {
        toast("The chat couldn't be started. Try again.");
        setDraft(question);
        return;
      }
    }
    startTurn(qc, id, { question, viewer, model: model || undefined });
    if (!chatId) void navigate({ to: "/ask/$chatId", params: { chatId: id }, replace: true });
    focusComposer();
  };

  if (chatId && chatQ.isError)
    return (
      <section className="view ask">
        <div className="turn">
          <div className="empty" role="alert">
            <MessageCircleOff aria-hidden />
            <p>This chat isn't available. It may have been deleted, or it hasn't been shared with you.</p>
            <Button variant="primary" className="mt" onClick={() => void navigate({ to: "/ask" })}>New chat</Button>
          </div>
        </div>
      </section>
    );

  const title = chat?.title ?? "New chat";
  return (
    <section className="view ask">
      <header className="ask-head">
        <h1 title={title}>{title}</h1>
        <div className="ask-actions" />
      </header>
      {chatId && chatQ.isPending && !pending ? <p className="sub ask-load" role="status">Loading…</p> : (
        <Thread turns={turns} who={initials(me?.username)} onRetry={(q) => void send(q)} scrollKey={chatId} />
      )}
      <Composer
        scope={scope} onScope={(s) => (chatId ? patch.mutate({ scope: s }) : setNewScope(s))}
        model={model} models={models?.models ?? []} onModel={setModel}
        streaming={!!streaming} onSend={(q) => void send(q)} onStop={() => chatId && stopTurn(chatId)}
      />
    </section>
  );
}
