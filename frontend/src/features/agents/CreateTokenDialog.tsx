import { Copy } from "lucide-react";
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import type { CreatedToken } from "@/api/agents";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { createToken, invalidText } from "./api";

export function CreateTokenDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const [shown, setShown] = useState(false);
  const change = (o: boolean) => { if (!o) setShown(false); onOpenChange(o); };
  return (
    <Dialog open={open} onOpenChange={change}>
      {/* while the secret is shown, a stray outside click must not lose it: only Done or Esc close */}
      <DialogContent onInteractOutside={(e) => { if (shown) e.preventDefault(); }}>
        {/* Radix unmounts closed content, so the secret held in TokenForm's state is dropped on close */}
        <TokenForm onDone={() => change(false)} onCreated={() => setShown(true)} />
      </DialogContent>
    </Dialog>
  );
}

function TokenForm({ onDone, onCreated }: { onDone: () => void; onCreated: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [write, setWrite] = useState(false);
  const [days, setDays] = useState("30");
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState<CreatedToken | null>(null);
  const n = Number(days);
  const daysOk = Number.isInteger(n) && n >= 1 && n <= 365;
  const nameOk = name.trim().length >= 1 && name.trim().length <= 100;

  const submit = async () => {
    setBusy(true);
    try {
      const t = await createToken({ name: name.trim(), scopes: write ? ["read", "write"] : ["read"], expires_in_days: n });
      setCreated(t);
      onCreated();
      void qc.invalidateQueries({ queryKey: ["tokens"] });
    } catch (e) {
      toast(e instanceof ApiError && e.status === 422 ? invalidText(e) : "The token couldn't be created. Try again.");
    } finally { setBusy(false); }
  };

  if (created) {
    const copy = async () => {
      try { await navigator.clipboard.writeText(created.token); toast("Token copied"); } catch { toast("Copying isn't available here. Select the token and copy it."); }
    };
    return (
      <>
        <DialogTitle>Token created</DialogTitle>
        <DialogDescription>Copy it now. You won't see it again: rOracle keeps only a hash.</DialogDescription>
        <div className="tok-secret">
          <input aria-label="Your new token" readOnly value={created.token} onFocus={(e) => e.currentTarget.select()} />
          <Button onClick={() => void copy()} aria-label="Copy token"><Copy aria-hidden />Copy</Button>
        </div>
        <p className="chipin-help">Give it to your agent as a bearer token for the rOracle MCP server. It can {write ? "read and write" : "only read"}, and stops working {new Date(created.expires_at).toLocaleDateString([], { dateStyle: "medium" })}.</p>
        <div className="foot"><Button variant="primary" onClick={onDone}>Done</Button></div>
      </>
    );
  }

  return (
    <form className="agent-form" onSubmit={(e) => { e.preventDefault(); if (nameOk && daysOk && !busy) void submit(); }}>
      <DialogTitle>New access token</DialogTitle>
      <DialogDescription>Lets an agent use rOracle as you, over MCP. It sees only what you can see, and never has admin rights.</DialogDescription>
      <label>Name<input value={name} maxLength={100} onChange={(e) => setName(e.target.value)} placeholder="Laptop agent" /></label>
      <fieldset>
        <legend>Access</legend>
        <label className="ck"><input type="radio" name="scope" checked={!write} onChange={() => setWrite(false)} />Read only: search, ask, load handoffs</label>
        <label className="ck"><input type="radio" name="scope" checked={write} onChange={() => setWrite(true)} />Read and write: also add handoff notes and propose changes</label>
      </fieldset>
      <label>Expires after (days)
        <input type="number" min={1} max={365} value={days} onChange={(e) => setDays(e.target.value)} aria-invalid={!daysOk} aria-describedby="tok-days-h" />
      </label>
      <p id="tok-days-h" className={daysOk ? "chipin-help" : "chipin-err"}>Choose 1 to 365 days.</p>
      <div className="foot">
        <Button variant="ghost" onClick={onDone}>Cancel</Button>
        <Button type="submit" variant="primary" disabled={!nameOk || !daysOk || busy}>Create token</Button>
      </div>
    </form>
  );
}
