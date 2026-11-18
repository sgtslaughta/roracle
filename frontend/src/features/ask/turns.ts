import type { ChatMessage, Citation, ExcludedByLabel, MessageStatus, Rating, RetrievedPassage } from "@/api/chat";
import type { Quote } from "./useAskContext";
import { excludedByMessage, noteByMessage, type PendingTurn } from "./askStore";

export interface AnswerData {
  text: string; citations: Citation[]; status: MessageStatus | "streaming"; detail?: string;
  /** server message id, set once the answer is saved */
  id?: string; retrieved: RetrievedPassage[]; rating?: Rating | null; excluded?: ExcludedByLabel[];
  progress?: string; note?: string;
}
/** `key` is positional so a pending turn keeps its identity when the saved messages replace it. */
export interface TurnData { key: string; question: string; quote: string | null; answer: AnswerData | null }

const quoteOf = (m: ChatMessage): string | null => m.viewer?.selection?.text ?? null;
export const EMPTY_ANSWER = "the language model returned an empty answer";

/** A saved assistant row as shown: an empty "complete" row (older servers) is a failure, not a blank answer. */
function savedStatus(m: ChatMessage): Pick<AnswerData, "status" | "detail"> {
  if (m.status === "complete" && !m.content.trim()) return { status: "failed", detail: EMPTY_ANSWER };
  return { status: m.status, detail: m.error ?? undefined };
}

/** True when the server history already holds the turn this pending one stood in for. */
export function isSettled(p: PendingTurn, messages: ChatMessage[]) {
  if (p.status === "streaming") return false;
  return (p.messageId ? messages.some((m) => m.id === p.messageId) : false) || messages.length >= p.base + 2;
}

let memo: { all: ChatMessage[]; n: number; turns: TurnData[] } | null = null;

/** Turns for the first `n` server messages; the same array (and turn objects) comes back until `all` changes, so memo(Turn) holds while a pending turn streams. */
function settledTurns(all: ChatMessage[], n: number): TurnData[] {
  if (memo && memo.all === all && memo.n === n) return memo.turns;
  const turns: TurnData[] = [];
  for (const m of all.slice(0, n)) {
    if (m.role === "user") turns.push({ key: `t${turns.length}`, question: m.content, quote: quoteOf(m), answer: null });
    else if (turns.length && !turns[turns.length - 1].answer)
      turns[turns.length - 1].answer = { text: m.content, citations: m.citations, ...savedStatus(m), id: m.id, retrieved: m.retrieved, rating: m.rating, excluded: excludedByMessage.get(m.id), note: noteByMessage.get(m.id) };
  }
  memo = { all, n, turns };
  return turns;
}

/** Pair user and assistant messages into turns; an unsettled pending turn replaces anything past `base`. */
export function buildTurns(all: ChatMessage[], pending: PendingTurn | undefined): TurnData[] {
  if (!pending || isSettled(pending, all)) return settledTurns(all, all.length);
  const turns = settledTurns(all, pending.base);
  const q: Quote | null = pending.selection;
  return [...turns, {
    key: `t${turns.length}`, question: pending.question, quote: q?.text ?? null,
    answer: { text: pending.text, citations: pending.citations, retrieved: [], excluded: pending.excluded, progress: pending.progress, note: pending.note, status: pending.status === "done" ? "complete" : pending.status, detail: pending.detail },
  }];
}
