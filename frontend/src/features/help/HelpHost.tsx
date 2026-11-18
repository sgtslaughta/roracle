import { lazy, Suspense, useEffect, useState } from "react";
import { useHelp } from "./helpStore";
import type { Pending } from "./actions";

// Lazy: the help UI, its Markdown renderer and the dialogs it can launch stay out of the entry chunk.
const HelpDialog = lazy(() => import("./HelpDialog"));
const CreateTokenDialog = lazy(() => import("@/features/agents/CreateTokenDialog").then((m) => ({ default: m.CreateTokenDialog })));
const HandoffDialog = lazy(() => import("@/features/agents/HandoffDialog").then((m) => ({ default: m.HandoffDialog })));

/** Mounted once in the Shell. A Try-it dialog opens after help has closed. */
export function HelpHost() {
  const { open } = useHelp();
  const [pending, setPending] = useState<Pending | null>(null);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => { if (open) setLoaded(true); }, [open]);
  useEffect(() => { if (open) setPending(null); }, [open]); // reopening help drops a stale dialog
  const done = (o: boolean) => { if (!o) setPending(null); };
  return (
    <Suspense fallback={null}>
      {(open || loaded) && <HelpDialog open={open} onPending={setPending} />}
      {!open && pending?.kind === "token" && <CreateTokenDialog open initialWrite onOpenChange={done} />}
      {!open && pending?.kind === "handoff" && <HandoffDialog open source={pending.source} onOpenChange={done} />}
    </Suspense>
  );
}
