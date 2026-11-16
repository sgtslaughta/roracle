export interface AiAssist { used: boolean; tools: string[]; description: string }
const FM = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?/;

/** Read ai_assistance when written as a one-line JSON flow mapping (what `withAi` writes); anything else reads as empty. */
export function readAi(content: string): AiAssist | null {
  const line = FM.exec(content)?.[1].split(/\r?\n/).find((l) => l.startsWith("ai_assistance:"));
  if (!line) return null;
  try {
    const o = JSON.parse(line.slice(line.indexOf(":") + 1));
    return { used: !!o.used, tools: Array.isArray(o.tools) ? o.tools.map(String) : [], description: String(o.description ?? "") };
  } catch { return null; }
}

/** Replace (or add) the `ai_assistance` key in the YAML front matter, leaving every other key alone. */
export function withAi(content: string, ai: AiAssist): string {
  const val = { used: ai.used, tools: ai.tools, description: ai.description };
  const line = `ai_assistance: ${JSON.stringify(val)}`;
  const m = FM.exec(content);
  if (!m) return `---\n${line}\n---\n${content}`;
  const kept: string[] = [];
  let skip = false;
  for (const l of m[1].split(/\r?\n/)) {
    if (l.startsWith("ai_assistance:")) { skip = true; continue; }
    if (skip && /^\s/.test(l)) continue;
    skip = false;
    kept.push(l);
  }
  return `---\n${[...kept, line].join("\n")}\n---\n${content.slice(m[0].length)}`;
}

export const stripFrontMatter = (content: string) => content.replace(FM, "");
