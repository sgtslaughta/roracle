export interface RecentDoc { id: string; title: string }
const KEY = "roracle-recent";
const MAX = 8;

export function getRecent(): RecentDoc[] {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? "[]");
    return Array.isArray(v) ? v.filter((d) => d && typeof d.id === "string" && typeof d.title === "string").slice(0, MAX) : [];
  } catch {
    return [];
  }
}

export function addRecent(d: RecentDoc) {
  try {
    localStorage.setItem(KEY, JSON.stringify([d, ...getRecent().filter((x) => x.id !== d.id)].slice(0, MAX)));
  } catch { /* storage unavailable */ }
}
