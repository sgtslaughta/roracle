import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { FileText, MessageCircle, Plus, Search, SunMoon } from "lucide-react";
import { useEffect, useMemo, useState, type ComponentType } from "react";
import { toast } from "sonner";
import { apiFetch } from "@/api/client";
import type { SearchResponse } from "@/api/types";
import { useChats } from "@/features/ask/api";
import { NAV } from "@/app/nav";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { openDoc } from "@/features/reader/useReaderStore";
import { shortCite } from "@/lib/cite";
import { stringifySearch } from "@/lib/searchParams";
import { cycleTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";
import { getRecent } from "./recent";
import { closePalette, usePaletteOpen } from "./usePalette";

interface Item { key: string; label: string; hint: string; icon: ComponentType<{ "aria-hidden"?: boolean }>; run: () => void }

export function CommandPalette() {
  const open = usePaletteOpen();
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const [debounced, setDebounced] = useState("");

  useEffect(() => { if (open) { setQ(""); setSel(0); } }, [open]);
  useEffect(() => { const t = setTimeout(() => setDebounced(q.trim()), 150); return () => clearTimeout(t); }, [q]);

  const chats = useChats().data;
  const found = useQuery({
    queryKey: ["palette-search", debounced],
    enabled: open && debounced.length >= 2,
    queryFn: () => apiFetch<SearchResponse>(`/search${stringifySearch({ q: debounced }, { size: 5, notes: false })}`),
  });

  const items = useMemo<Item[]>(() => {
    const needle = q.trim().toLowerCase();
    const match = (s: string) => !needle || s.toLowerCase().includes(needle);
    const pages: Item[] = NAV.filter((n) => match(n.label)).map((n) => ({ key: n.to, label: n.label, hint: "Page", icon: n.icon, run: () => void navigate({ to: n.to }) }));
    const actions: Item[] = [
      ...(match("New chat") ? [{ key: "new-chat", label: "New chat", hint: "Action", icon: Plus, run: () => void navigate({ to: "/ask" }) }] : []),
      ...(match("Switch theme") ? [{ key: "theme", label: "Switch theme", hint: "Action", icon: SunMoon, run: () => toast(`Theme: ${cycleTheme()}`) }] : []),
    ];
    const chatItems: Item[] = (chats ?? []).filter((c) => match(c.title)).slice(0, 5)
      .map((c) => ({ key: `chat-${c.id}`, label: c.title, hint: "Chat", icon: MessageCircle, run: () => void navigate({ to: "/ask/$chatId", params: { chatId: c.id } }) }));
    const docs = new Map<string, Item>();
    for (const r of getRecent().filter((d) => match(d.title))) docs.set(r.id, { key: r.id, label: r.title, hint: "Recent", icon: FileText, run: () => openDoc(r.id) });
    for (const r of found.data?.results ?? []) if (!docs.has(r.doc_id)) docs.set(r.doc_id, { key: r.doc_id, label: r.title, hint: shortCite(r.authors, r.year), icon: FileText, run: () => openDoc(r.doc_id) });
    return [...pages, ...actions, ...chatItems, ...docs.values()];
  }, [q, found.data, chats, navigate]);

  const active = Math.min(sel, Math.max(0, items.length - 1));
  const pick = (it?: Item) => { if (!it) return; closePalette(); it.run(); };

  return (
    <Dialog open={open} onOpenChange={(o) => !o && closePalette()}>
      <DialogContent bare aria-describedby={undefined}>
        <DialogTitle className="sr-only">Command menu</DialogTitle>
        <div className="palin">
          <Search aria-hidden />
          <input
            role="combobox" aria-expanded aria-controls="pal-list" aria-activedescendant={items[active] ? `pal-${active}` : undefined}
            aria-label="Command" placeholder="Jump to a page or document" value={q} autoFocus
            onChange={(e) => { setQ(e.target.value); setSel(0); }}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                e.preventDefault();
                setSel((active + (e.key === "ArrowDown" ? 1 : -1) + items.length) % Math.max(1, items.length));
              } else if (e.key === "Enter") { e.preventDefault(); pick(items[active]); }
            }}
          />
        </div>
        <ul id="pal-list" role="listbox" aria-label="Results" className="pallist">
          {items.map((it, i) => (
            <li key={it.key} id={`pal-${i}`} role="option" aria-selected={i === active} className={cn(i === active && "sel")} onMouseMove={() => setSel(i)} onClick={() => pick(it)}>
              <it.icon aria-hidden /><span className="lbl" title={it.label}>{it.label}</span><span className="h">{it.hint}</span>
            </li>
          ))}
          {!items.length && <li className="none" role="presentation">No matches</li>}
        </ul>
      </DialogContent>
    </Dialog>
  );
}
