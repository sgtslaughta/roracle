import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useLocation, useNavigate } from "@tanstack/react-router";
import { ExternalLink, Link as LinkIcon, X } from "lucide-react";
import { toast } from "sonner";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { useMe } from "@/features/library/useMe";
import { useOnboarding } from "@/features/onboarding/api";
import { useReaderStore } from "@/features/reader/useReaderStore";
import { runTry, type Pending } from "./actions";
import { loadAll, topicTitle } from "./content";
import { HelpBody } from "./HelpBody";
import { HelpTree } from "./HelpTree";
import { closeHelp, openHelp, useHelp } from "./helpStore";
import { setHash } from "./useHelpHash";
import { findTopic, GROUPS, topicForPath, visibleTopics } from "./manifest";
import { parseTopic, type TrySeg } from "./parse";

const DEFAULT = "getting-started";

/** Stays mounted once loaded so Radix closes it properly (focus return, scroll unlock); the sheet's state resets on close. */
export default function HelpDialog({ open, onPending }: { open: boolean; onPending(p: Pending): void }) {
  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) closeHelp(); }}>
      <DialogContent
        bare aria-describedby={undefined}
        className="help-sheet top-1/2 w-[min(1140px,calc(100vw-32px))] -translate-y-1/2 max-[760px]:left-0 max-[760px]:top-0 max-[760px]:w-full max-[760px]:translate-x-0 max-[760px]:translate-y-0"
      >
        <Sheet onPending={onPending} />
      </DialogContent>
    </Dialog>
  );
}

function Sheet({ onPending }: { onPending(p: Pending): void }) {
  const { topic: want, section } = useHelp();
  const { data: me } = useMe();
  const isAdmin = me?.is_admin ?? false;
  const pats = useOnboarding().data?.pats_enabled ?? true;
  const pathname = useLocation({ select: (l) => l.pathname });
  const navigate = useNavigate();
  const qc = useQueryClient();
  const activeDoc = useReaderStore().active;
  const [all, setAll] = useState<Record<string, string> | null>(null);
  const [q, setQ] = useState("");
  const body = useRef<HTMLDivElement>(null);

  const topics = useMemo(() => visibleTopics(isAdmin, pats), [isAdmin, pats]);
  const here = findTopic(topicForPath(pathname), isAdmin, pats)?.id ?? DEFAULT;
  const id = (want && findTopic(want, isAdmin, pats)?.id) || here;
  const md = all?.[id];
  const doc = useMemo(() => (md ? parseTopic(md) : null), [md]);
  const titles = useMemo(() => Object.fromEntries(Object.entries(all ?? {}).map(([k, v]) => [k, topicTitle(v)])), [all]);

  useEffect(() => { let live = true; void loadAll().then((a) => live && setAll(a)); return () => { live = false; }; }, []);
  useEffect(() => { setHash(`#help-${id}`); }, [id]);
  useEffect(() => { if (body.current && !section) body.current.scrollTop = 0; }, [id, section]);
  useEffect(() => { if (md && section) document.getElementById(section)?.scrollIntoView({ block: "start" }); }, [md, section]);

  const needle = q.trim().toLowerCase();
  const shown = useMemo(
    () => (!needle || !all ? topics : topics.filter((t) => (titles[t.id] ?? "").toLowerCase().includes(needle) || (all[t.id] ?? "").toLowerCase().includes(needle))),
    [topics, needle, all, titles],
  );

  const onGo = useCallback((t: string, s?: string) => openHelp(t, s), []);
  const onTry = (seg: TrySeg) => runTry(seg, { navigate, qc, activeDoc, setPending: onPending });
  const scrollTo = (sid: string) => document.getElementById(sid)?.scrollIntoView({ block: "start" });
  const copyLink = async () => {
    const link = `${location.origin}/library#help-${id}`;
    try { await navigator.clipboard.writeText(link); toast("Link copied. Anyone who opens it lands on this topic."); } catch { toast(link); }
  };

  const idx = topics.findIndex((t) => t.id === id);
  const prev = idx > 0 ? topics[idx - 1] : undefined;
  const next = idx >= 0 ? topics[idx + 1] : undefined;
  const group = topics[idx]?.group ?? "";

  return (
    <>
        <div className="shead">
          <DialogTitle>Help</DialogTitle>
          <input className="hsearch" type="search" aria-label="Search help" placeholder="Search help" value={q} onChange={(e) => setQ(e.target.value)} />
          <div className="tools">
            <a className="ghost full" href="/docs/" target="_blank" rel="noopener"><ExternalLink size={13} aria-hidden /> Open full docs</a>
            <button type="button" className="close" aria-label="Close help (Esc)" onClick={closeHelp}><X size={18} aria-hidden /></button>
          </div>
        </div>
        <div className="mnav">
          <select aria-label="Help topic" value={id} onChange={(e) => openHelp(e.target.value)}>
            {GROUPS.map((g) => {
              const ts = topics.filter((t) => t.group === g.name);
              return ts.length ? <optgroup key={g.name} label={g.name}>{ts.map((t) => <option key={t.id} value={t.id}>{titles[t.id] || t.id}</option>)}</optgroup> : null;
            })}
          </select>
        </div>
        {shown.length === 0 ? (
          <nav className="snav" aria-label="Help topics"><p style={{ padding: 12 }}>No topic matches “{q.trim()}”. Try fewer words.</p></nav>
        ) : (
          <HelpTree topics={shown} titles={titles} current={id} here={here} sections={doc?.sections ?? []} onPick={onGo} onSection={scrollTo} />
        )}
        <div className="body" ref={body}>
          <div className="crumb">
            <span>Help</span><span className="sep">/</span><span>{group}</span>
            <button type="button" className="copyl" onClick={() => void copyLink()}><LinkIcon size={12} aria-hidden /> Copy link to this topic</button>
          </div>
          {md ? (
            <HelpBody md={md} file={findTopic(id, isAdmin, pats)?.file} onGo={onGo} onTry={onTry} tryDisabled={(s) => s.action === "handoff" && !activeDoc} />
          ) : <p role="status">Loading…</p>}
          <div className="pager">
            {prev && <button type="button" onClick={() => onGo(prev.id)}><small>Previous</small>{titles[prev.id] || prev.id}</button>}
            {next && <button type="button" className="next" onClick={() => onGo(next.id)}><small>Next</small>{titles[next.id] || next.id}</button>}
          </div>
        </div>
    </>
  );
}
