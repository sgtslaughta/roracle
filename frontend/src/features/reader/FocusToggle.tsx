import { Maximize2, Minimize2 } from "lucide-react";
import { cycleFocus, type Focus } from "./useReaderStore";

const LABEL: Record<Focus, string> = { split: "split view", "three-quarter": "three-quarter width", full: "full screen" };

export function FocusToggle({ focus }: { focus: Focus }) {
  const Icon = focus === "full" ? Minimize2 : Maximize2;
  return (
    <button className="icon-btn" aria-label={`Focus mode, now ${LABEL[focus]}`} title={`Focus mode: ${LABEL[focus]} (Esc steps back)`} aria-pressed={focus !== "split"} onClick={cycleFocus}>
      <Icon aria-hidden />
    </button>
  );
}
