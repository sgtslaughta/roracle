import { Bot } from "lucide-react";
import { useState } from "react";
import type { HandoffManifest } from "@/api/agents";
import { Button, type ButtonProps } from "@/components/ui/button";
import { HandoffDialog, type HandoffSource } from "./HandoffDialog";

/** "Continue in agent" button. `source()` is read when clicked, so prefill reflects the current state. */
export function ContinueInAgent({ source, variant, onCreated }: { source: () => HandoffSource; variant?: ButtonProps["variant"]; onCreated?: (m: HandoffManifest) => void }) {
  const [src, setSrc] = useState<HandoffSource | null>(null);
  return (
    <>
      <Button variant={variant} onClick={() => setSrc(source())}><Bot aria-hidden />Continue in agent</Button>
      {src && <HandoffDialog source={src} open onOpenChange={(o) => !o && setSrc(null)} onCreated={onCreated} />}
    </>
  );
}
