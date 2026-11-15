import { useState } from "react";
import type { ChatScope } from "@/api/chat";
import { Popover, PopoverAnchor, PopoverContent } from "@/components/ui/popover";
import { useCollections } from "@/features/library/api";
import { useReaderStore } from "@/features/reader/useReaderStore";

const CAP = 20;
const MORE = "__more";

/** "Search in" select. Lists at most 20 collections (plus the current one); the rest are behind a filterable popover. */
export function ScopeSelect({ scope, onScope }: { scope: ChatScope; onScope: (s: ChatScope) => void }) {
  const { tabs } = useReaderStore();
  const collections = useCollections().data ?? [];
  const [more, setMore] = useState(false);
  const [q, setQ] = useState("");
  const current = scope.startsWith("collection:") ? scope.slice("collection:".length) : null;
  const shown = collections.slice(0, CAP);
  const extra = current && !shown.some((c) => c.id === current) ? collections.filter((c) => c.id === current) : [];
  const rest = collections.slice(CAP).filter((c) => c.id !== current);
  const hits = rest.filter((c) => c.name.toLowerCase().includes(q.trim().toLowerCase()));
  const pick = (s: ChatScope) => { setMore(false); setQ(""); onScope(s); };

  return (
    <Popover open={more} onOpenChange={(o) => { setMore(o); if (!o) setQ(""); }}>
      <PopoverAnchor asChild>
        <select className="sel" aria-label="Search in" value={scope} onChange={(e) => (e.target.value === MORE ? setMore(true) : onScope(e.target.value as ChatScope))}>
          <option value="all">All documents you can see</option>
          <option value="open" disabled={!tabs.length && scope !== "open"}>{tabs.length || scope !== "open" ? "Open documents only" : "Open documents only (none open)"}</option>
          {[...shown, ...extra].map((c) => <option key={c.id} value={`collection:${c.id}`}>{`Collection: ${c.name}`}</option>)}
          {rest.length > 0 && <option value={MORE}>More collections…</option>}
        </select>
      </PopoverAnchor>
      <PopoverContent className="ovl-menu" aria-label="More collections">
        <input className="ovl-filter" type="search" placeholder="Filter" aria-label="Filter collections" value={q} onChange={(e) => setQ(e.target.value)} />
        <div className="ovl-scroll">
          {hits.map((c) => <button key={c.id} type="button" className="ovl-item" onClick={() => pick(`collection:${c.id}`)}>{c.name}</button>)}
          {!hits.length && <p className="ovl-empty">No matches</p>}
        </div>
      </PopoverContent>
    </Popover>
  );
}
