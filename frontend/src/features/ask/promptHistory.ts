const CAP = 100;
const key = (userKey: string) => `roracle.prompts.${userKey}`;

/** Sent prompts for this user, oldest first. Storage unavailable or corrupt -> no history. */
export function loadHistory(userKey: string): string[] {
  try {
    const v: unknown = JSON.parse(localStorage.getItem(key(userKey)) ?? "[]");
    return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
  } catch { return []; }
}

/** Append a sent prompt: skips empty text and a repeat of the newest entry; keeps the last 100. */
export function pushHistory(userKey: string, text: string): void {
  if (!text.trim()) return;
  const h = loadHistory(userKey);
  if (h[h.length - 1] === text) return;
  try { localStorage.setItem(key(userKey), JSON.stringify([...h, text].slice(-CAP))); } catch { /* private mode */ }
}
