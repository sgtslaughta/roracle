import { isAvailable, type Citation } from "@/api/chat";

/** "## question", the answer, then one reference line per citation in n order. */
export function answerMarkdown(question: string, content: string, citations: Citation[]): string {
  const refs = [...citations].sort((a, b) => a.n - b.n).map((c) => {
    if (!isAvailable(c)) return `[${c.n}] Source not available`;
    const parts = [c.authors.length ? c.authors.join(", ") : null, c.title, c.year, `p. ${c.page}`];
    return `[${c.n}] ${parts.filter((x) => x != null).join(", ")}`;
  });
  return `## ${question.replace(/\s+/g, " ").trim()}\n\n${content}\n\n${refs.join("\n")}`;
}
