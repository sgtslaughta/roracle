export type TryAction = "search" | "ask" | "uploads" | "handoff" | "token" | "card" | "docs";
export type TrySeg = { kind: "try"; action: TryAction; label: string; hint?: string; q?: string };
export type Segment =
  | { kind: "md"; text: string }
  | { kind: "callout"; tone: "tip" | "warning" | "note"; title: string; text: string }
  | { kind: "dl"; items: { term: string; def: string }[] }
  | { kind: "steps"; steps: { title: string; id: string; body: Segment[] }[] }
  | { kind: "tabs"; tabs: { name: string; caption: string; lang: string; code: string }[] }
  | TrySeg;
export interface Section { id: string; title: string; depth: 2 | 3 }

/** python-markdown toc slug: NFKD, drop non word/space/hyphen, lowercase, collapse separators. */
export const slugify = (text: string): string =>
  text.normalize("NFKD").replace(/[^\w\s-]/g, "").trim().toLowerCase().replace(/[-\s]+/g, "-");

const DEFAULT_TITLE = { tip: "Tip", warning: "Heads up", note: "Note" } as const;
const FENCE = /^\s*(`{3,}|~{3,})(.*)$/;
const TRY = /^<!--\s*try:(search|ask|uploads|handoff|token|card|docs)((?:\s+\w+=(?:"[^"]*"|'[^']*'))*)\s*-->\s*$/;
const plain = (s: string) => s.replace(/\[([^\]]*)\]\([^)]*\)/g, "$1").replace(/[`*_]/g, "").trim();

/** Fence-aware line scanner: tracks whether line i is inside a fenced block. */
function inFence(lines: string[]): boolean[] {
  const out: boolean[] = []; let open: string | null = null;
  for (const l of lines) {
    const m = FENCE.exec(l);
    if (open) { out.push(true); if (m && m[1][0] === open[0] && m[1].length >= open.length && !m[2].trim()) open = null; }
    else if (m) { open = m[1]; out.push(true); }
    else out.push(false);
  }
  return out;
}

function parseTabs(lines: string[]): Extract<Segment, { kind: "tabs" }>["tabs"] {
  const tabs: ReturnType<typeof parseTabs> = [];
  for (let i = 0; i < lines.length; i++) {
    const m = FENCE.exec(lines[i]);
    if (!m) continue;
    const info = m[2].trim(), lang = info.split(/\s+/)[0] ?? "";
    const title = /title="([^"]*)"/.exec(info)?.[1] ?? lang;
    const [name, ...rest] = title.split(" | ");
    const code: string[] = [];
    for (i++; i < lines.length && !FENCE.test(lines[i]); i++) code.push(lines[i]);
    tabs.push({ name: name.trim(), caption: (rest.join(" | ") || name).trim(), lang, code: code.join("\n") });
  }
  return tabs;
}

function parseSteps(lines: string[]): Extract<Segment, { kind: "steps" }>["steps"] {
  const fenced = inFence(lines);
  const steps: ReturnType<typeof parseSteps> = [];
  let cur: { title: string; lines: string[] } | null = null;
  const done = () => { if (cur) steps.push({ title: cur.title, id: slugify(plain(cur.title)), body: parseSegments(cur.lines) }); };
  lines.forEach((l, i) => {
    const h = !fenced[i] && /^###\s+(.+?)\s*#*\s*$/.exec(l);
    if (h) { done(); cur = { title: plain(h[1]), lines: [] }; } else cur?.lines.push(l);
  });
  done();
  return steps;
}

function parseSegments(lines: string[]): Segment[] {
  const fenced = inFence(lines);
  const segs: Segment[] = [];
  let buf: string[] = [];
  const flush = () => {
    while (buf.length && !buf[0].trim()) buf.shift();
    while (buf.length && !buf[buf.length - 1].trim()) buf.pop();
    if (buf.length) segs.push({ kind: "md", text: buf.join("\n") + "\n" });
    buf = [];
  };
  const isDef = (i: number) => i + 1 < lines.length && !fenced[i + 1] && /^:\s+\S/.test(lines[i + 1]);
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i];
    if (fenced[i]) { buf.push(l); continue; }
    let m: RegExpExecArray | null;
    if ((m = /^!!!\s+(tip|warning|note)(?:\s+"([^"]*)")?\s*$/.exec(l))) {
      flush();
      const body: string[] = [];
      while (i + 1 < lines.length && /^( {4}|\t)/.test(lines[i + 1])) body.push(lines[++i].replace(/^( {4}|\t)/, ""));
      const tone = m[1] as "tip" | "warning" | "note";
      segs.push({ kind: "callout", tone, title: m[2] ?? DEFAULT_TITLE[tone], text: body.join("\n").trim() });
    } else if (/^<!--\s*(tabs|steps)\s*-->\s*$/.test(l)) {
      const kind = /tabs/.test(l) ? "tabs" : "steps";
      flush();
      const inner: string[] = [];
      for (i++; i < lines.length && !new RegExp(`^<!--\\s*/${kind}\\s*-->`).test(lines[i]); i++) inner.push(lines[i]);
      if (kind === "tabs") segs.push({ kind, tabs: parseTabs(inner) }); else segs.push({ kind, steps: parseSteps(inner) });
    } else if ((m = TRY.exec(l))) {
      flush();
      const a: Record<string, string> = {};
      for (const x of m[2].matchAll(/(\w+)=(?:"([^"]*)"|'([^']*)')/g)) a[x[1]] = x[2] ?? x[3];
      segs.push({ kind: "try", action: m[1] as TryAction, label: a.label ?? "Try it", ...(a.hint ? { hint: a.hint } : {}), ...(a.q ? { q: a.q } : {}) });
    } else if (l.trim() && !/^[#>:\-*+\s`|<]/.test(l) && isDef(i)) {
      flush();
      const items: { term: string; def: string }[] = [];
      while (i < lines.length && lines[i].trim() && isDef(i)) {
        const def = [lines[i + 1].replace(/^:\s+/, "")];
        const term = lines[i].trim();
        i += 1;
        while (i + 1 < lines.length && /^ {4}\S/.test(lines[i + 1])) def.push(lines[++i].trim());
        items.push({ term, def: def.join(" ") });
        let j = i + 1;
        while (j < lines.length && !lines[j].trim()) j++;
        if (j < lines.length && !fenced[j] && lines[j].trim() && isDef(j)) i = j; else break;
      }
      segs.push({ kind: "dl", items });
    } else buf.push(l);
  }
  flush();
  return segs;
}

export function parseTopic(md: string): { title: string; lede: string; segments: Segment[]; sections: Section[] } {
  const lines = md.split("\n");
  const fenced = inFence(lines);
  const sections: Section[] = [];
  lines.forEach((l, i) => {
    const h = !fenced[i] && /^(#{2,3})\s+(.+?)\s*#*\s*$/.exec(l);
    if (h) { const title = plain(h[2]); sections.push({ id: slugify(title), title, depth: h[1].length as 2 | 3 }); }
  });
  const t = lines.findIndex((l, i) => !fenced[i] && /^#\s+/.test(l));
  const title = t < 0 ? "" : plain(lines[t].replace(/^#\s+/, ""));
  let i = t + 1;
  while (i < lines.length && !lines[i].trim()) i++;
  const lede: string[] = [];
  if (i < lines.length && !/^(#|!!!|<!--|```|~~~|[-*+>|:]\s)/.test(lines[i])) {
    for (; i < lines.length && lines[i].trim(); i++) lede.push(lines[i].trim());
  }
  return { title, lede: lede.join(" "), segments: parseSegments(lines.slice(i)), sections };
}
