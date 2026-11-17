import type { ReactNode } from "react";
import { splitQuery } from "./queryBuilder";

/** Highlight the words of the positive words and phrases; excluded terms and quotes are ignored. */
export const searchTerms = (q: string) => {
  const { words, phrases } = splitQuery(q);
  return [words, ...phrases].join(" ").toLowerCase().split(/[^\p{L}\p{N}]+/u).filter((t) => t.length > 2);
};

/** Wrap query terms in <mark>. Pure text in, React nodes out (no HTML injection). */
export function highlight(text: string, terms: string[]): ReactNode {
  if (!terms.length) return text;
  const re = new RegExp(`(${terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "gi");
  return text.split(re).map((part, i) => (i % 2 ? <mark key={i}>{part}</mark> : part));
}

/**
 * Server highlight: escaped text with <em> around matches. Only <em>/</em> are honoured; every other tag is
 * dropped to plain text. Text is decoded through the DOM (textContent), then rendered as React text nodes.
 */
export function safeHighlight(html: string): ReactNode {
  const doc = new DOMParser().parseFromString(`<body>${html}</body>`, "text/html");
  const out: ReactNode[] = [];
  const walk = (node: Node, inEm: boolean) => {
    node.childNodes.forEach((c) => {
      if (c.nodeType === Node.TEXT_NODE) {
        const t = c.textContent ?? "";
        if (t) out.push(inEm ? <mark key={out.length}>{t}</mark> : t);
      } else if (c.nodeType === Node.ELEMENT_NODE) {
        const tag = (c as Element).tagName.toLowerCase();
        if (tag === "script" || tag === "style") return;
        walk(c, inEm || tag === "em");
      }
    });
  };
  walk(doc.body, false);
  return out;
}
