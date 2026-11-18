import { useId, useState } from "react";
import { toast } from "sonner";

export function Snippet({ label, code }: { label: string; code: string }) {
  const copy = async () => {
    try { await navigator.clipboard.writeText(code); toast("Copied."); }
    catch { toast("Copying isn't available here. Select the text instead."); }
  };
  return (
    <figure className="snip">
      <figcaption>{label}<button type="button" onClick={copy}>Copy</button></figcaption>
      <pre><code>{code}</code></pre>
    </figure>
  );
}

export interface Tab { name: string; caption: string; code: string }

export function Tabs({ tabs }: { tabs: Tab[] }) {
  const [i, setI] = useState(0);
  const id = useId();
  const cur = tabs[i] ?? tabs[0];
  if (!cur) return null;
  const key = (e: React.KeyboardEvent) => {
    const d = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!d) return;
    e.preventDefault();
    const n = (i + d + tabs.length) % tabs.length;
    setI(n);
    document.getElementById(`${id}-${n}`)?.focus();
  };
  return (
    <>
      <div className="tabs" role="tablist" onKeyDown={key}>
        {tabs.map((t, n) => (
          <button key={t.name} type="button" role="tab" id={`${id}-${n}`} aria-selected={n === i} aria-controls={`${id}-p`} tabIndex={n === i ? 0 : -1} onClick={() => setI(n)}>{t.name}</button>
        ))}
      </div>
      <div role="tabpanel" id={`${id}-p`} aria-labelledby={`${id}-${i}`}><Snippet label={cur.caption} code={cur.code} /></div>
    </>
  );
}
