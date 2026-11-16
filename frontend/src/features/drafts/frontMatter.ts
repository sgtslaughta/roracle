export interface AiAssist { used: boolean; tools: string[]; description: string }
// same shape as the backend (writing/markdown.py _FM): opening --- line, then up to the first line that is only ---
const FM = /^---[ \t]*\r?\n(?:([\s\S]*?)\r?\n)?---[ \t]*(?:\r?\n|$)/;

/** Read ai_assistance when written as a one-line JSON flow mapping (what `withAi` writes); anything else reads as empty. */
export function readAi(content: string): AiAssist | null {
  const line = FM.exec(content)?.[1]?.split(/\r?\n/).find((l) => l.startsWith("ai_assistance:"));
  if (!line) return null;
  try {
    const o = JSON.parse(line.slice(line.indexOf(":") + 1));
    return { used: !!o.used, tools: Array.isArray(o.tools) ? o.tools.map(String) : [], description: String(o.description ?? "") };
  } catch { return null; }
}

/** Replace (or add) the `ai_assistance` key in the YAML front matter, leaving every other key alone. */
export function withAi(content: string, ai: AiAssist): string {
  const val = { used: ai.used, tools: ai.tools, description: ai.description };
  // PyYAML rejects raw C1 controls, lone surrogates and U+FFFE/FFFF even inside quotes: escape them
  const json = JSON.stringify(val).replace(/[\u007f-\u009f\ufffe\uffff]|[\ud800-\udbff](?![\udc00-\udfff])|(?<![\ud800-\udbff])[\udc00-\udfff]/g, (c) => `\\u${c.charCodeAt(0).toString(16).padStart(4, "0")}`);
  const line = `ai_assistance: ${json}`;
  const m = FM.exec(content);
  if (!m) return `---\n${line}\n---\n${content}`;
  const kept: string[] = [];
  let skip = false;
  for (const l of m[1] ? m[1].split(/\r?\n/) : []) {
    if (l.startsWith("ai_assistance:")) { skip = true; continue; }
    if (skip && /^\s/.test(l)) continue;
    skip = false;
    kept.push(l);
  }
  return `---\n${[...kept, line].join("\n")}\n---\n${content.slice(m[0].length)}`;
}

export const stripFrontMatter = (content: string) => content.replace(FM, "");
