/** Remark plugin: turns `[3]`, `[2,3]`, `[2][3]` in text into `cite-ref` nodes. */
const RE = /\[(?:(?:passage|source)s?\s+)?(\d+(?:\s*,\s*\d+)*)\]/gi; // models sometimes write [Passage 2]
const SKIP = new Set(["code", "inlineCode", "math", "inlineMath", "link", "linkReference"]);

interface MNode { type: string; value?: string; children?: MNode[]; data?: Record<string, unknown> }

const cite = (n: string): MNode => ({ type: "cite", data: { hName: "cite-ref", hProperties: { n } } });

function split(value: string): MNode[] | null {
  const out: MNode[] = [];
  let last = 0;
  for (const m of value.matchAll(RE)) {
    if (m.index > last) out.push({ type: "text", value: value.slice(last, m.index) });
    for (const n of m[1].split(",")) out.push(cite(n.trim()));
    last = m.index + m[0].length;
  }
  if (!out.length) return null;
  if (last < value.length) out.push({ type: "text", value: value.slice(last) });
  return out;
}

function walk(node: MNode) {
  if (!node.children || SKIP.has(node.type)) return;
  node.children = node.children.flatMap((c) => {
    if (c.type === "text" && c.value) return split(c.value) ?? [c];
    walk(c);
    return [c];
  });
}

export const remarkCiteMarkers = () => (tree: MNode) => walk(tree);

/** Citation numbers in order of first appearance, unique. */
export function citeNumbers(text: string): number[] {
  const seen = new Set<number>();
  const plain = text.replace(/```[\s\S]*?(```|$)|~~~[\s\S]*?(~~~|$)|`[^`\n]*`|\$\$[\s\S]*?\$\$|\$[^$\n]*\$/g, " ");
  for (const m of plain.matchAll(RE)) for (const n of m[1].split(",")) seen.add(Number(n));
  return [...seen];
}
