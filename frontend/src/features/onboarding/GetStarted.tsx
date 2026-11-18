import { Check, X } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { errorText } from "@/api/client";
import { openHelp } from "@/features/help/helpStore";
import { setDismissed, useOnboarding } from "./api";
import { AGENTS, BASICS, type Step } from "./steps";

export function GetStarted() {
  const { data } = useOnboarding();
  const qc = useQueryClient();
  if (!data || data.dismissed) return null;
  const done = new Set(data.steps);
  const agents = AGENTS.filter((s) => data.pats_enabled || (s.id !== "token" && s.id !== "mcp"));
  const inBasics = !BASICS.every((s) => done.has(s.id));
  const shown: Step[] = inBasics ? BASICS : agents;
  const n = shown.filter((s) => done.has(s.id)).length;
  if (n === shown.length) return null;
  const hide = async () => {
    await setDismissed(true, qc)
      .then(() => toast("Hidden. Reopen it from Help, Your first hour."))
      .catch((e) => toast(errorText(e)));
  };
  return (
    <section className="card get-started" aria-labelledby="gs-h">
      <header>
        <h2 id="gs-h">{inBasics ? "Get started" : "Connect your agents"}</h2>
        <span className="prog">{n} of {shown.length}</span>
        <button type="button" className="x" aria-label="Hide Get started. Reopen it from Help." onClick={hide}><X aria-hidden size={16} /></button>
      </header>
      <div className="bar" aria-hidden><i style={{ width: `${(n / shown.length) * 100}%` }} /></div>
      <ul className="steps">
        {shown.map((s) => {
          const d = done.has(s.id);
          return (
            <li key={s.id} className={d ? "done" : undefined}>
              <span className="tick">{d && <Check aria-hidden size={12} />}</span>
              <span className="lbl">
                <span className="sr-only">{d ? "Done:" : "Not done yet:"} </span>{s.title}
                <small>{s.hint}</small>
              </span>
              <button type="button" className="link" aria-label={`Show me: ${s.title}`} onClick={() => openHelp(s.topic, s.section)}>Show me</button>
            </li>
          );
        })}
      </ul>
      {inBasics && <p className="next-group">Next: connect your agents. It opens when these four are done.</p>}
    </section>
  );
}
