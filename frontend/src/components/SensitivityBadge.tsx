import { ShieldHalf } from "lucide-react";
import type { Label } from "@/api/types";
import { useLabels } from "@/features/labels/api";
import { cn } from "@/lib/utils";

/** WCAG relative luminance of a #rrggbb colour. */
function luminance(hex: string) {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
}
/** Black or white, whichever contrasts more with the label colour. */
export const textOn = (hex: string) => (/^#[0-9a-f]{6}$/i.test(hex) && luminance(hex) > 0.179 ? "#111" : "#fff");

/** Sensitivity label: colour and name, never colour alone. Tooltip carries the admin's description. */
export function SensitivityBadge({ label, own, className }: { label: Label; own?: boolean; className?: string }) {
  const desc = useLabels().data?.find((l) => l.id === label.id)?.description;
  const bg = /^#[0-9a-f]{6}$/i.test(label.colour) ? label.colour : "#496580";
  return (
    <span
      className={cn("slabel", className)} style={{ background: bg, color: textOn(bg) }} data-label={label.id}
      title={`${own ? "Note label" : "Sensitivity"}: ${label.name}${desc ? `. ${desc}` : ""}`}
    >
      <ShieldHalf aria-hidden /><span className="sr-only">{own ? "Note label: " : "Sensitivity: "}</span><span className="t">{label.name}</span>
    </span>
  );
}
