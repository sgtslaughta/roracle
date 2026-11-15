import { useEffect } from "react";
import { ReaderPane } from "@/features/reader/ReaderPane";
import { ReaderTabs } from "@/features/reader/ReaderTabs";
import { MIN_WIDTH, setWidth, stepBack, useReaderStore } from "@/features/reader/useReaderStore";

/** Mounts the reader in the shell grid, owns the resize grip and the Esc step-back. */
export function ReaderHost() {
  const state = useReaderStore();
  const tab = state.tabs.find((t) => t.docId === state.active);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      if (document.querySelector('[role="dialog"], [role="menu"]')) return; // let overlays close first
      const t = e.target as HTMLElement | null;
      if (t && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)) return;
      if (stepBack()) e.preventDefault();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  // resizable only in split view; the 3/4 level is sized by CSS
  useEffect(() => {
    const app = document.getElementById("app");
    if (!app) return;
    if (state.focus === "split") app.style.setProperty("--reader", `${state.width}px`);
    else app.style.removeProperty("--reader");
  }, [state.focus, state.width]);

  if (!tab) return null;
  const startDrag = (e: React.PointerEvent<HTMLDivElement>) => {
    const grip = e.currentTarget;
    grip.setPointerCapture(e.pointerId);
    const move = (m: PointerEvent) => setWidth(Math.max(MIN_WIDTH, Math.min(window.innerWidth - 560, window.innerWidth - m.clientX)));
    const up = () => { grip.removeEventListener("pointermove", move); grip.removeEventListener("pointerup", up); };
    grip.addEventListener("pointermove", move);
    grip.addEventListener("pointerup", up);
  };
  return (
    <aside className="reader" aria-label="Reader">
      {state.focus === "split" && <div className="grip" title="Drag to resize" onPointerDown={startDrag} />}
      <ReaderTabs state={state} />
      <ReaderPane key={tab.docId} tab={tab} view={state.view} />
    </aside>
  );
}
