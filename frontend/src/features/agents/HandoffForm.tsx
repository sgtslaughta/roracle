import { useRef, useState } from "react";
import type { Audience, HandoffShare } from "@/api/agents";
import { Button } from "@/components/ui/button";
import { PeopleInput } from "@/components/PeopleInput";
import { ChipInput } from "@/features/ask/ChipInput";

export interface HandoffValues { title: string; goal: string; brief: string; audience: Audience; shares: HandoffShare[] | null; days: number | null }
interface Props {
  initial: { title: string; goal: string; brief: string; audience?: Audience };
  /** create: expiry required (default 7); edit: "Keep current" allowed */
  mode: "create" | "edit";
  titlePlaceholder?: string;
  busy?: boolean;
  submitLabel: string;
  onSubmit: (v: HandoffValues) => void;
  onCancel: () => void;
}

export const AUDIENCE_LABEL: Record<Audience, string> = { creator: "Only me", shared: "Named people or groups", authenticated: "Everyone signed in" };
export const MAX_SHARES = 100;
const MAX_PRINCIPAL = 200;
const tooLong = (v: string) => (v.length > MAX_PRINCIPAL ? `Keep each entry under ${MAX_PRINCIPAL} characters.` : null);
const groupError = (v: string) => tooLong(v) ?? (v.startsWith("/") ? null : "Group paths start with “/”, for example /Mathematics/Spectral Theory.");

/** Title, goal, brief, audience (+ shares) and expiry. Everything typed here is shown to agents and recipients as plain text. */
export function HandoffForm({ initial, mode, titlePlaceholder, busy, submitLabel, onSubmit, onCancel }: Props) {
  const [title, setTitle] = useState(initial.title);
  const [goal, setGoal] = useState(initial.goal);
  const [brief, setBrief] = useState(initial.brief);
  const [audience, setAudience] = useState<Audience>(initial.audience ?? "creator");
  const [groups, setGroups] = useState<string[]>([]);
  const [users, setUsers] = useState<string[]>([]);
  const [days, setDays] = useState(mode === "create" ? "7" : "");
  const gRef = useRef<() => string[] | null>(null);
  // editing an already-shared handoff: the API doesn't list current shares, and omitting them keeps them
  const keepShares = mode === "edit" && initial.audience === "shared";
  const needShares = audience === "shared" && !keepShares && !groups.length && !users.length;
  const total = groups.length + users.length;
  const tooMany = audience === "shared" && total > MAX_SHARES;
  const full = () => (total >= MAX_SHARES ? `At most ${MAX_SHARES} people and groups in all.` : null);

  const submit = () => {
    let shares: HandoffShare[] | null = null;
    if (audience === "shared") {
      const g = gRef.current ? gRef.current() : groups, u = users;
      if (!g || !u || g.length + u.length > MAX_SHARES) return;
      if (g.length || u.length) shares = [...g.map((p) => ({ kind: "group" as const, principal: p })), ...u.map((p) => ({ kind: "user" as const, principal: p }))];
      else if (!keepShares) return;
    }
    onSubmit({ title: title.trim(), goal: goal.trim(), brief, audience, shares, days: days ? Number(days) : null });
  };

  return (
    <div className="agent-form">
      <label>Title<input value={title} maxLength={200} placeholder={titlePlaceholder} onChange={(e) => setTitle(e.target.value)} /></label>
      <label>Goal<input value={goal} maxLength={2000} onChange={(e) => setGoal(e.target.value)} placeholder="What should the agent do next?" /></label>
      <label>Brief the agent starts from
        <textarea className="ta" value={brief} maxLength={20000} onChange={(e) => setBrief(e.target.value)} />
      </label>
      <div className="agent-row">
        <label>Who can load it
          <select className="sel" value={audience} onChange={(e) => setAudience(e.target.value as Audience)}>
            {(Object.keys(AUDIENCE_LABEL) as Audience[]).map((a) => <option key={a} value={a}>{AUDIENCE_LABEL[a]}</option>)}
          </select>
        </label>
        <label>Expires
          <select className="sel" value={days} onChange={(e) => setDays(e.target.value)}>
            {mode === "edit" && <option value="">Keep current</option>}
            {[1, 7, 30, 90].map((d) => <option key={d} value={d}>In {d} day{d > 1 ? "s" : ""}{mode === "edit" ? " from now" : ""}</option>)}
          </select>
        </label>
      </div>
      {audience === "shared" && (
        <>
          <ChipInput label="Groups" values={groups} onChange={setGroups} validate={(v) => groupError(v) ?? full()} commitRef={gRef} placeholder="/Mathematics/Spectral Theory" />
          <PeopleInput label="People" values={users} onChange={setUsers} full={full()} />
          {tooMany && <p className="chipin-err" role="alert">At most {MAX_SHARES} people and groups in all. Remove {total - MAX_SHARES}.</p>}
          <p className="chipin-help">{keepShares ? "Leave both empty to keep the current list; adding anyone replaces it." : "Press Enter to add. Add at least one person or group."}</p>
        </>
      )}
      <p className="chipin-help">Agents still sign in, and see only the documents their user can see.</p>
      <div className="foot">
        <Button variant="ghost" onClick={onCancel}>Cancel</Button>
        <Button variant="primary" onClick={submit} disabled={busy || needShares || tooMany}>{submitLabel}</Button>
      </div>
    </div>
  );
}
