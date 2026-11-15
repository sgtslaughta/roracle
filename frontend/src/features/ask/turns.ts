import type { ChatMessage, Citation, MessageStatus, RetrievedPassage } from "@/api/chat";
import type { Quote } from "./useAskContext";
import type { PendingTurn } from "./askStore";

export interface AnswerData {
  text: string; citations: Citation[]; status: MessageStatus | "streaming"; detail?: string;
  /** server message id, set once the answer is saved */
  id?: string; retrieved: RetrievedPassage[]; rating?: 1 | -1 | null;
}
export interface TurnData { key: string; question: string; quote: string | null; answer: AnswerData | null }

const quoteOf = (m: ChatMessage): string | null => m.viewer?.selection?.text ?? null;

/** True when the server history already holds the turn this pending one stood in for. */
export function isSettled(p: PendingTurn, messages: ChatMessage[]) {
  if (p.status === "streaming") return false;
  return (p.messageId ? messages.some((m) => m.id === p.messageId) : false) || messages.length >= p.base + 2;
}

/** Pair user and assistant messages into turns; an unsettled pending turn replaces anything past `base`. */
export function buildTurns(all: ChatMessage[], pending: PendingTurn | undefined): TurnData[] {
  const messages = pending && !isSettled(pending, all) ? all.slice(0, pending.base) : all;
  const turns: TurnData[] = [];
  for (const m of messages) {
    if (m.role === "user") turns.push({ key: m.id, question: m.content, quote: quoteOf(m), answer: null });
    else if (turns.length && !turns[turns.length - 1].answer)
      turns[turns.length - 1].answer = { text: m.content, citations: m.citations, status: m.status, id: m.id, retrieved: m.retrieved, rating: m.rating };
  }
  if (pending && !isSettled(pending, all)) {
    const q: Quote | null = pending.selection;
    turns.push({
      key: "pending", question: pending.question, quote: q?.text ?? null,
      answer: { text: pending.text, citations: pending.citations, retrieved: [], status: pending.status === "done" ? "complete" : pending.status, detail: pending.detail },
    });
  }
  return turns;
}
