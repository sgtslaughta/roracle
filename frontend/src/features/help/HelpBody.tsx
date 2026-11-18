import { Fragment, lazy, Suspense, useMemo, type ReactNode } from "react";
import type { Components } from "react-markdown";
import { Info, Lightbulb, TriangleAlert } from "lucide-react";
import { safeHref } from "@/components/SafeMarkdown";
import { docsUrl, screenshot } from "./content";
import { TOPICS } from "./manifest";
import { parseTopic, slugify, type Segment, type TrySeg } from "./parse";
import { Snippet, Tabs } from "./Snippet";

const loadMd = () => import("@/components/MarkdownBody");
const MarkdownBody = lazy(loadMd);
void loadMd(); // start fetching as soon as help opens, so raw markdown never flashes
const PLACEHOLDER = "https://roracle.example.internal";

interface Hast { type: string; tagName?: string; value?: string; children?: Hast[]; data?: { meta?: string } }
const hastText = (n: Hast): string => n.value ?? (n.children ?? []).map(hastText).join("");
const textOf = (c: ReactNode): string =>
  Array.isArray(c) ? c.map(textOf).join("") : typeof c === "string" || typeof c === "number" ? String(c)
    : c && typeof c === "object" && "props" in c ? textOf((c.props as { children?: ReactNode }).children) : "";

/** Resolve a relative link against the topic's directory (paths are relative to the docs root). */
function resolve(file: string, href: string): string {
  const out = file.split("/").slice(0, -1);
  for (const p of href.split("/")) { if (p === "..") out.pop(); else if (p && p !== ".") out.push(p); }
  return out.join("/");
}

function buildComponents(file: string, onGo: (topic: string, section?: string) => void): Components {
  return {
    h2: ({ node: _n, children }) => <h2 id={slugify(textOf(children))}>{children}</h2>,
    h3: ({ node: _n, children }) => <h3 id={slugify(textOf(children))}>{children}</h3>,
    p: ({ node, children }) => {
      const only = node?.children.length === 1 && (node.children[0] as Hast).tagName === "img";
      return only ? <>{children}</> : <p>{children}</p>;
    },
    img: ({ node: _n, src, alt }) => {
      const url = screenshot((src ?? "").split("/").pop() ?? "");
      return url
        ? <figure className="shot"><img src={url} alt={alt ?? ""} loading="lazy" /><figcaption>{alt}</figcaption></figure>
        : <>{alt ?? ""}</>;
    },
    pre: ({ node, children }) => {
      const code = (node?.children as Hast[] | undefined)?.[0];
      const title = /title="([^"]*)"/.exec(code?.data?.meta ?? "")?.[1];
      return title !== undefined && code ? <Snippet label={title} code={hastText(code).replace(/\n$/, "")} /> : <pre>{children}</pre>;
    },
    a: ({ node: _n, href, children }) => {
      if (href && !/^([a-z][a-z0-9+.-]*:|[/#])/i.test(href) && /\.md(#|$)/.test(href)) {
        const [path, frag] = href.split("#");
        const rel = resolve(file, path);
        const topic = TOPICS.find((t) => t.file === rel);
        if (topic) return <a href={`#help-${topic.id}`} onClick={(e) => { e.preventDefault(); onGo(topic.id, frag); }}>{children}</a>;
        return <a href={docsUrl(rel) + (frag ? `#${frag}` : "")} target="_blank" rel="noopener noreferrer">{children}</a>;
      }
      const s = safeHref(href);
      if (!s) return <span>{children}</span>;
      if (href?.startsWith("#")) {
        return <a href={s.href} onClick={(e) => { e.preventDefault(); document.getElementById(href.slice(1))?.scrollIntoView?.({ block: "start" }); }}>{children}</a>;
      }
      return s.external ? <a href={s.href} target="_blank" rel="noopener noreferrer nofollow">{children}</a> : <a href={s.href}>{children}</a>;
    },
    table: ({ node: _n, ...p }) => <div className="tbl"><table {...p} /></div>,
  };
}

function Md({ text, components }: { text: string; components: Components }) {
  return (
    <Suspense fallback={<div data-md-fallback className="prose-skel" aria-busy="true"><i /><i /><i /></div>}>
      <MarkdownBody text={text} components={components} />
    </Suspense>
  );
}

const ICON = { tip: Lightbulb, warning: TriangleAlert, note: Info } as const;

export interface HelpBodyProps {
  md: string;
  /** Topic file relative to the docs root (e.g. `user/chat.md`); relative links resolve against its directory. */
  file?: string;
  onGo(topic: string, section?: string): void;
  onTry(seg: TrySeg): void;
  /** Try-it buttons that cannot run right now (rendered disabled, hint still shown). */
  tryDisabled?(seg: TrySeg): boolean;
}

export function HelpBody({ md, file = "user/_.md", onGo, onTry, tryDisabled }: HelpBodyProps) {
  const doc = useMemo(() => parseTopic(md.replaceAll(PLACEHOLDER, location.origin)), [md]);
  const components = useMemo(() => buildComponents(file, onGo), [file, onGo]);
  const M = (text: string) => <Md text={text} components={components} />;
  const seg = (s: Segment, k: number): ReactNode => {
    switch (s.kind) {
      case "md": return <div key={k} className="prose">{M(s.text)}</div>;
      case "callout": {
        const Icon = ICON[s.tone];
        return (
          <aside key={k} className={s.tone === "warning" ? "call warn" : "call"}>
            <Icon size={16} aria-hidden style={{ marginTop: 2 }} />
            <div><b>{s.title}</b>{M(s.text)}</div>
          </aside>
        );
      }
      case "dl": return <dl key={k} className="dl">{s.items.map((x) => <Fragment key={x.term}><dt>{x.term}</dt><dd>{M(x.def)}</dd></Fragment>)}</dl>;
      case "steps":
        return (
          <ol key={k} className="walk">
            {s.steps.map((st) => <li key={st.id}><h3 id={st.id}>{st.title}</h3><div className="stepbody">{st.body.map(seg)}</div></li>)}
          </ol>
        );
      case "tabs": return <Tabs key={k} tabs={s.tabs} />;
      case "try":
        return (
          <div key={k} className="try">
            <button type="button" className="prim" disabled={tryDisabled?.(s)} onClick={() => onTry(s)}>{s.label}</button>
            {s.hint && <span>{s.hint}</span>}
          </div>
        );
    }
  };
  return (
    <>
      <h1>{doc.title}</h1>
      {doc.lede && <div className="lede">{M(doc.lede)}</div>}
      {doc.segments.map(seg)}
    </>
  );
}
