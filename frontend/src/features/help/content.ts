import { TOPICS } from "./manifest";

// Markdown is lazy (one chunk per file); screenshots are bundled Vite assets (air-gapped: nothing external).
const docs = import.meta.glob<string>(["@docs/user/*.md", "@docs/glossary.md", "@docs/admin/*.md"], { query: "?raw", import: "default" });
const shots = import.meta.glob<string>("@docs/assets/screens/*.png", { query: "?url", import: "default", eager: true });

const loaderFor = (file: string) => Object.entries(docs).find(([k]) => k.endsWith(`/${file}`))?.[1];

export function loadTopic(id: string): Promise<string> {
  const t = TOPICS.find((x) => x.id === id);
  const load = t && loaderFor(t.file);
  return load ? load() : Promise.reject(new Error(`Unknown help topic: ${id}`));
}

export async function loadAll(): Promise<Record<string, string>> {
  const out: Record<string, string> = {};
  await Promise.all(TOPICS.map(async (t) => { out[t.id] = await loadTopic(t.id); }));
  return out;
}

export const screenshot = (file: string): string | undefined => Object.entries(shots).find(([k]) => k.endsWith(`/${file}`))?.[1];

/** Docs-site URL for a path relative to the docs root (README maps to its directory). */
export function docsUrl(rel: string): string {
  const p = rel.replace(/\.md$/, "").replace(/(^|\/)README$/, "$1");
  return `/docs/${p}${p === "" || p.endsWith("/") ? "" : "/"}`;
}

export const topicTitle = (md: string): string => /^#\s+(.+?)\s*$/m.exec(md)?.[1] ?? "";
