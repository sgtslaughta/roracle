import { Bot, CheckCheck, Inbox } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useBulkApprove, useProposals, type Proposal, type ProposalStatus } from "./api";
import { ProposalCard } from "./ProposalCard";

const EMPTY: Record<ProposalStatus, string> = {
  pending: "No proposals waiting. When an agent suggests a note, it shows up here for you to approve.",
  approved: "Nothing approved yet.",
  rejected: "Nothing rejected.",
};

function byClient(items: Proposal[]) {
  const m = new Map<string, Proposal[]>();
  for (const p of items) m.set(p.client, [...(m.get(p.client) ?? []), p]);
  return [...m];
}

export function ProposalsPage() {
  const [status, setStatus] = useState<ProposalStatus>("pending");
  const { data, isPending, isError } = useProposals(status);
  const bulk = useBulkApprove();
  const [sel, setSel] = useState<Set<string>>(new Set());
  const items = data?.items ?? [];
  const pending = status === "pending";
  const toggle = (ids: string[], on: boolean) => setSel((s) => { const n = new Set(s); ids.forEach((i) => (on ? n.add(i) : n.delete(i))); return n; });

  const approveMany = (ids: string[]) => bulk.mutate(ids, {
    onSuccess: ({ approved, failed }) => {
      setSel(new Set(failed.map((f) => f.proposal_id))); // keep what failed selected for a retry
      if (!failed.length) toast(`Approved ${approved.length} ${approved.length === 1 ? "proposal" : "proposals"}.`);
      else toast(`Approved ${approved.length}. ${failed.length} couldn't be approved: ${failed[0].detail}`);
    },
  });
  const selected = items.filter((p) => sel.has(p.id)).map((p) => p.id);

  return (
    <section className="view">
      <div className="uhead-row">
        <div>
          <h1>Agent proposals</h1>
          <p className="sub">Notes your agents want to add. Nothing is published until you approve it.</p>
        </div>
        {pending && items.length > 0 && (
          <div className="row2">
            <Button disabled={!selected.length || bulk.isPending} onClick={() => approveMany(selected)}>Approve selected{selected.length > 0 && ` (${selected.length})`}</Button>
            <Button variant="primary" disabled={bulk.isPending} onClick={() => approveMany(items.map((p) => p.id))}><CheckCheck aria-hidden />Approve all</Button>
          </div>
        )}
      </div>
      <Tabs value={status} onValueChange={(v) => { setStatus(v as ProposalStatus); setSel(new Set()); }}>
        <TabsList aria-label="Proposal status">
          <TabsTrigger value="pending">Pending</TabsTrigger>
          <TabsTrigger value="approved">Approved</TabsTrigger>
          <TabsTrigger value="rejected">Rejected</TabsTrigger>
        </TabsList>
      </Tabs>
      <div className="plist">
        {isPending ? <p className="sub">Loading…</p>
          : isError ? <p className="sub" role="alert">Proposals couldn't be loaded. Try again.</p>
          : !items.length ? <div className="empty"><Inbox aria-hidden /><p>{EMPTY[status]}</p></div>
          : byClient(items).map(([client, ps]) => {
              const ids = ps.map((p) => p.id);
              const all = ids.every((i) => sel.has(i));
              return (
                <section key={client} className="pgroup" aria-label={`From ${client}`}>
                  <div className="pghead">
                    <div><b><Bot aria-hidden />{client}</b><div className="sub">{ps.length} {pending ? "waiting" : status}</div></div>
                    {pending && (
                      <label className="row2 sub">
                        <Checkbox checked={all} onCheckedChange={(c) => toggle(ids, c === true)} aria-label={`Select all from ${client}`} />Select all
                      </label>
                    )}
                  </div>
                  {ps.map((p) => <ProposalCard key={p.id} p={p} selected={sel.has(p.id)} onSelect={pending ? (on) => toggle([p.id], on) : undefined} />)}
                </section>
              );
            })}
        {data && data.total > items.length && <p className="sub">Showing the newest {items.length} of {data.total}. Decide these to see the rest.</p>}
      </div>
    </section>
  );
}
