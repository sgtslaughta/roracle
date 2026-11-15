import { isAvailable, type Citation } from "@/api/chat";

export interface DocInfo { authors: string[]; year: number | null }

/** "## question", the answer, then one reference line per citation in n order. */
export function answerMarkdown(question: string, content: string, citations: Citation[], docs: Record<string, DocInfo | undefined>): string {
  const refs = [...citations].sort((a, b) => a.n - b.n).map((c) => {
    if (!isAvailable(c)) return `[${c.n}] Source not available`;
    const d = docs[c.doc_id];
    const parts = [d?.authors.length ? d.authors.join(", ") : null, c.title, d?.year ?? null, `p. ${c.page}`];
    return `[${c.n}] ${parts.filter((x) => x != null).join(", ")}`;
  });
  return `## ${question}\n\n${content}\n\n${refs.join("\n")}`;
}
