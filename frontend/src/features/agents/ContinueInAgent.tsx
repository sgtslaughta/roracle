import { Bot } from "lucide-react";
import { useState } from "react";
import type { HandoffManifest } from "@/api/agents";
import { Button, type ButtonProps } from "@/components/ui/button";
import { HandoffDialog, type HandoffSource } from "./HandoffDialog";

/** "Continue in agent" button. `source()` is read when clicked, so prefill reflects the current state. */
export function ContinueInAgent({ source, variant, onCreated, disabled }: { source: () => HandoffSource; variant?: ButtonProps["variant"]; onCreated?: (m: HandoffManifest) => void; disabled?: boolean }) {
  const [src, setSrc] = useState<HandoffSource | null>(null);
  return (
    <>
      <Button variant={variant} title="Continue in agent" disabled={disabled} onClick={() => setSrc(source())}><Bot aria-hidden /><span className="tlbl">Continue in agent</span></Button>
      {src && <HandoffDialog source={src} open onOpenChange={(o) => !o && setSrc(null)} onCreated={onCreated} />}
    </>
  );
}
