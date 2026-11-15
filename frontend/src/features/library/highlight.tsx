import type { ReactNode } from "react";

export const searchTerms = (q: string) => q.toLowerCase().split(/[^\p{L}\p{N}]+/u).filter((t) => t.length > 2);

/** Wrap query terms in <mark>. Pure text in, React nodes out (no HTML injection). */
export function highlight(text: string, terms: string[]): ReactNode {
  if (!terms.length) return text;
  const re = new RegExp(`(${terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "gi");
  return text.split(re).map((part, i) => (i % 2 ? <mark key={i}>{part}</mark> : part));
}
