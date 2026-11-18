import { useRef, type KeyboardEvent } from "react";
import { GROUPS, type Topic } from "./manifest";
import type { Section } from "./parse";

interface Props {
  topics: Topic[];
  titles: Record<string, string>;
  current: string;
  /** Topic of the page behind the dialog: gets the "this page" pill. */
  here: string;
  sections: Section[];
  onPick(id: string): void;
  onSection(id: string): void;
}

/** Group > topic > section tree. Up/Down move focus between its buttons. */
export function HelpTree({ topics, titles, current, here, sections, onPick, onSection }: Props) {
  const nav = useRef<HTMLElement>(null);
  const onKey = (e: KeyboardEvent) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    const bs = [...(nav.current?.querySelectorAll<HTMLElement>(".grp > summary, .tree button") ?? [])];
    const i = bs.indexOf(document.activeElement as HTMLElement);
    if (!bs.length || i < 0) return;
    e.preventDefault();
    bs[Math.max(0, Math.min(bs.length - 1, i + (e.key === "ArrowDown" ? 1 : -1)))].focus();
  };
  return (
    <nav className="snav" aria-label="Help topics" ref={nav} onKeyDown={onKey}>
      {GROUPS.map(({ name, icon: Icon }) => {
        const ts = topics.filter((t) => t.group === name);
        if (!ts.length) return null;
        return (
          <details className="grp" key={name} open>
            <summary><span className="chev" aria-hidden>▶</span><span className="gicon" aria-hidden><Icon size={12} /></span>{name}<span className="gcount">{ts.length}</span></summary>
            <ul className="tree">
              {ts.map((t) => (
                <li key={t.id}>
                  <button type="button" aria-current={t.id === current ? "page" : undefined} onClick={() => onPick(t.id)}>
                    {titles[t.id] || t.id}{t.id === here && <span className="here">this page</span>}
                  </button>
                  {t.id === current && sections.length > 0 && (
                    <ul className="secs">
                      {sections.map((s) => <li key={s.id}><button type="button" onClick={() => onSection(s.id)}>{s.title}</button></li>)}
                    </ul>
                  )}
                </li>
              ))}
            </ul>
          </details>
        );
      })}
    </nav>
  );
}
