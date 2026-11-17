export type QueryParts = { words: string; phrases: string[]; excluded: string[] };

const TOKEN = /(-?)"([^"]*)"|(\S+)/g;
const squash = (s: string) => s.split(/\s+/).filter(Boolean).join(" ");

/** Same grammar as backend `parse_query`: quotes pair left to right; a lone `-` or unbalanced quote is plain text. */
export function splitQuery(q: string): QueryParts {
  const words: string[] = [], phrases: string[] = [], excluded: string[] = [];
  for (const m of q.matchAll(TOKEN)) {
    const [, neg, quoted, bare] = m;
    if (bare !== undefined) {
      if (bare.startsWith("-") && bare.length > 1 && !bare.includes('"')) excluded.push(bare.slice(1));
      else words.push(bare);
    } else if (squash(quoted)) (neg ? excluded : phrases).push(squash(quoted));
  }
  return { words: words.join(" "), phrases, excluded };
}

export function joinQuery(p: QueryParts): string {
  const ex = (e: string) => (/\s/.test(e) ? `-"${e}"` : `-${e}`);
  return [
    squash(p.words),
    ...p.phrases.map(squash).filter(Boolean).map((s) => `"${s}"`),
    ...p.excluded.map(squash).filter(Boolean).map(ex),
  ].filter(Boolean).join(" ");
}
