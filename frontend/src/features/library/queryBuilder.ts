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

const unq = (s: string) => squash(s.replaceAll('"', " "));

/**
 * Words, then phrases, then exclusions. Only a word that starts with `"` or `-"` could open a quote pair, and
 * splitQuery yields at most one (a trailing unbalanced quote); then the structured parts go first so nothing
 * after it holds a quote. Word order and characters are never changed.
 */
export function joinQuery(p: QueryParts): string {
  const ex = (e: string) => (/\s/.test(e) ? `-"${e}"` : `-${e}`);
  const words = squash(p.words);
  const rest = [
    ...p.phrases.map(unq).filter(Boolean).map((s) => `"${s}"`),
    ...p.excluded.map(unq).filter(Boolean).map(ex),
  ];
  const opens = words.split(" ").some((t) => t.startsWith('"') || t.startsWith('-"'));
  return (opens ? [...rest, words] : [words, ...rest]).filter(Boolean).join(" ");
}
