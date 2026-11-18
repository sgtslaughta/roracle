/** Page heading for a query: strips one pair of wrapping quotes, never nests quotes. */
export function resultsHeading(q: string): string {
  let t = q.trim();
  if (!t) return "Library";
  if (/^"[^"]*"$/.test(t)) t = t.slice(1, -1).trim();
  if (!t) return "Library";
  return t.includes('"') ? `Results for ${t}` : `Results for “${t}”`;
}
