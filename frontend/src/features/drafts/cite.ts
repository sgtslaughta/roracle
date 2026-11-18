/** Draft citation syntax (backend writing/markdown.py + cite_check.py): `[@doc:<id>]`, `[@psg:<version id>:<n>]`, grouped with `;`. */
const UUID = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";
const BRACKET = /\[[^[\]@]*@[^[\]]*\]/g; // same shape as the backend's _BRACKET
const DOC = new RegExp(`@doc:(${UUID})`, "g");

export const docCitation = (id: string) => `[@doc:${id}]`;

/** Distinct paper ids cited with `@doc:` inside citation brackets, in order of first use (at most `max`). */
export function citedDocIds(text: string, max = 50): string[] {
  const out = new Set<string>();
  for (const b of text.match(BRACKET) ?? []) for (const m of b.matchAll(DOC)) if (out.size < max) out.add(m[1].toLowerCase());
  return [...out];
}

const esc = (s: string) => s.replace(/[\\`*_{}[\]()#+\-.!<>|~$]/g, "\\$&");

/** Preview text: each `@doc:<id>` in a citation bracket becomes the paper's title (ids without a known title stay as written). */
export function withCiteTitles(text: string, titles: Record<string, string>): string {
  return text.replace(BRACKET, (b) => {
    if (!new RegExp(`@doc:${UUID}`).test(b)) return b;
    const inner = b.slice(1, -1).split(";").map((k) => {
      const m = k.trim().match(new RegExp(`^@doc:(${UUID})$`));
      const t = m && titles[m[1].toLowerCase()];
      return t ? `*${esc(t)}*` : esc(k.trim());
    });
    return `\\[${inner.join("; ")}\\]`;
  });
}
