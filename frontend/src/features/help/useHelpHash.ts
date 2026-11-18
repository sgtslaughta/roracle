import { useEffect, useRef } from "react";
import { helpStore, openHelp } from "./helpStore";

const PREFIX = "#help-";

/** `#help-<topic>` or `#help-<topic>--<section>`; anything else is not ours. */
export function parseHelpHash(hash: string): { topic: string; section?: string } | null {
  if (!hash.startsWith(PREFIX) || hash.length === PREFIX.length) return null;
  const [topic, section] = hash.slice(PREFIX.length).split("--");
  return { topic, section: section || undefined };
}

/** Rewrite only the hash, keeping TanStack Router's history state intact. */
export function setHash(hash: string) {
  const { pathname, search } = location;
  history.replaceState(history.state, "", pathname + search + hash);
}

/** Opens help from `#help-…` (on load and on hashchange) and clears the hash when help closes. */
export function useHelpHash() {
  const open = helpStore.use((s) => s.open);
  useEffect(() => {
    const sync = () => {
      const h = parseHelpHash(location.hash);
      if (h) openHelp(h.topic, h.section);
    };
    sync();
    window.addEventListener("hashchange", sync);
    return () => window.removeEventListener("hashchange", sync);
  }, []);
  const was = useRef(open);
  useEffect(() => {
    if (was.current && !open && parseHelpHash(location.hash)) setHash("");
    was.current = open;
  }, [open]);
}
