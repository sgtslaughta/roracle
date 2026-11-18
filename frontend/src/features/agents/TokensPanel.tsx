import { fmtDate } from "@/lib/dates";
import { KeyRound, Plus } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import type { AccessToken } from "@/api/agents";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { useRevokeToken, useTokens } from "./api";
import { CreateTokenDialog } from "./CreateTokenDialog";
import { copyAction, RowMenu } from "@/components/RowMenu";

const PAGE = 20;
const stateOf = (t: AccessToken) => (t.revoked_at ? "Revoked" : new Date(t.expires_at) <= new Date() ? "Expired" : "Active");

export function TokensPanel() {
  const { data, isError } = useTokens();
  const revoke = useRevokeToken();
  const [creating, setCreating] = useState(false);
  const [target, setTarget] = useState<AccessToken | null>(null);
  const [shown, setShown] = useState(PAGE);

  const doRevoke = () => {
    if (!target) return;
    revoke.mutate(target.id, {
      onSuccess: () => toast(`Revoked “${target.name}”. Agents using it are signed out.`),
      onError: () => toast("The token couldn't be revoked. Try again."),
      onSettled: () => setTarget(null),
    });
  };

  return (
    <div className="agent-panel">
      <div className="uhead-row">
        <p className="sub">Tokens let agents reach rOracle over MCP as you. Manage them here; they can't manage themselves.</p>
        <Button variant="primary" onClick={() => setCreating(true)}><Plus aria-hidden />New token</Button>
      </div>
      {isError && <p role="alert">Tokens couldn't be loaded. Reload to try again.</p>}
      {data && !data.length && <div className="empty"><KeyRound aria-hidden /><p>No tokens yet. Create one to connect an agent.</p></div>}
      {data && data.length > 0 && (
        <div className="tok-wrap">
          <table className="agent-table">
            <thead><tr><th>Name</th><th>Prefix</th><th>Access</th><th>Expires</th><th>Last used</th><th>State</th><th><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>
              {data.slice(0, shown).map((t) => {
                const st = stateOf(t);
                return (
                  <tr key={t.id} className={st !== "Active" ? "dim" : undefined}>
                    <td className="trunc" title={t.name}>{t.name}</td>
                    <td><code>{t.prefix}…</code></td>
                    <td>{t.scopes.includes("write") ? "Read and write" : "Read"}</td>
                    <td>{fmtDate(t.expires_at)}</td>
                    <td>{fmtDate(t.last_used_at)}</td>
                    <td><span className={`pill ${st === "Active" ? "done" : st === "Revoked" ? "failed" : "neutral"}`}>{st}</span></td>
                    <td>
                      {st === "Active" && <Button variant="ghost" aria-label={`Revoke ${t.name}`} onClick={() => setTarget(t)}>Revoke</Button>}
                      <RowMenu kebab={false} label={`Actions for token ${t.name}`} actions={[copyAction("Copy prefix", t.prefix), ...(st !== "Active" ? [] : [{ label: "Revoke", danger: true, onSelect: () => setTarget(t) }])]} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {data && data.length > shown && <Button variant="ghost" onClick={() => setShown(shown + PAGE)}>Show {Math.min(PAGE, data.length - shown)} more</Button>}
      {creating && <CreateTokenDialog open={creating} onOpenChange={setCreating} />}
      <ConfirmDialog open={!!target} onOpenChange={(o) => !o && setTarget(null)} title="Revoke this token?" confirm="Revoke" onConfirm={doRevoke} busy={revoke.isPending}>
        Agents using “{target?.name}” stop working at once. This can't be undone.
      </ConfirmDialog>
    </div>
  );
}
