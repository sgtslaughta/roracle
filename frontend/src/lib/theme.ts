import { useSyncExternalStore } from "react";

export type Theme = "system" | "light" | "dark";
export const THEME_KEY = "roracle-theme";
const ORDER: Theme[] = ["system", "light", "dark"];

export function getTheme(): Theme {
  try {
    const v = localStorage.getItem(THEME_KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

const subs = new Set<() => void>();
export const subscribeTheme = (f: () => void) => { subs.add(f); return () => { subs.delete(f); }; };

export function applyTheme(t: Theme) {
  const el = document.documentElement;
  if (t === "system") el.removeAttribute("data-theme");
  else el.setAttribute("data-theme", t);
}

export function setTheme(t: Theme) {
  try {
    localStorage.setItem(THEME_KEY, t);
  } catch {
    /* storage unavailable: theme still applies for this session */
  }
  applyTheme(t);
  subs.forEach((f) => f());
}

export function cycleTheme(): Theme {
  const next = ORDER[(ORDER.indexOf(getTheme()) + 1) % ORDER.length];
  setTheme(next);
  return next;
}

/** Reactive theme for components (top bar icon follows changes made from the command palette). */
export const useTheme = () => useSyncExternalStore(subscribeTheme, getTheme, () => "system" as Theme);
