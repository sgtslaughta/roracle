import { describe, expect, it } from "vitest";
import type { ChatMessage } from "@/api/chat";
import { buildTurns, EMPTY_ANSWER } from "./turns";

const msg = (o: Partial<ChatMessage>): ChatMessage => ({ id: "m", role: "assistant", content: "", citations: [], retrieved: [], status: "complete", model: null, created_at: "", ...o });

describe("saved answers", () => {
  it("an empty complete answer is shown as failed, never as a blank answer", () => {
    const [t] = buildTurns([msg({ id: "u", role: "user", content: "q" }), msg({ id: "a", content: "  " })], undefined);
    expect(t.answer).toMatchObject({ status: "failed", detail: EMPTY_ANSWER });
  });
  it("a failed answer carries the server's reason", () => {
    const [t] = buildTurns([msg({ id: "u2", role: "user", content: "q" }), msg({ id: "a2", status: "failed", error: "the language model is busy" })], undefined);
    expect(t.answer).toMatchObject({ status: "failed", detail: "the language model is busy" });
  });
});
