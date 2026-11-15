import { useEffect } from "react";
import { openDoc } from "@/features/reader/useReaderStore";
import { togglePalette } from "@/features/palette/usePalette";

/** True when the key press belongs to a text field and must not trigger shortcuts. */
export function isTyping(el: EventTarget | null): boolean {
  const e = el as HTMLElement | null;
  if (!e || !e.tagName) return false;
  return /^(INPUT|TEXTAREA|SELECT)$/.test(e.tagName) || e.isContentEditable === true;
}

const resultEls = () => [...document.querySelectorAll<HTMLElement>("li.result")];

/** Global shortcuts: Ctrl/Cmd+K palette, `/` search, j/k move through results, Enter opens. Esc lives with the reader and dialogs. */
export function useGlobalKeys() {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        togglePalette();
        return;
      }
      if (e.metaKey || e.ctrlKey || e.altKey || isTyping(e.target)) return;
      if (document.querySelector('[role="dialog"]')) return;
      if (e.key === "/") {
        e.preventDefault();
        document.getElementById("global-search")?.focus();
      } else if (e.key === "j" || e.key === "k") {
        const rs = resultEls();
        if (!rs.length) return;
        const i = rs.findIndex((r) => r === document.activeElement || r.contains(document.activeElement));
        const next = e.key === "j" ? Math.min(rs.length - 1, i + 1) : Math.max(0, i < 0 ? 0 : i - 1);
        rs[next].focus();
        rs[next].scrollIntoView({ block: "nearest" });
      } else if (e.key === "Enter" && document.activeElement instanceof HTMLElement && document.activeElement.matches("li.result")) {
        const id = document.activeElement.dataset.doc;
        if (id) { e.preventDefault(); openDoc(id); }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);
}
