import { useNavigate, useRouterState } from "@tanstack/react-router";
import { useEffect, useRef, useState, type ReactNode } from "react";

/** Top-bar search: debounced 150 ms, writes `q` into the library URL (resets paging). */
export function GlobalSearch({ icon }: { icon: ReactNode }) {
  const navigate = useNavigate();
  const urlQ = useRouterState({ select: (s) => (s.location.pathname === "/library" ? ((s.location.search as { q?: string }).q ?? "") : null) });
  const [value, setValue] = useState(urlQ ?? "");
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);

  useEffect(() => {
    if (urlQ !== null) setValue(urlQ);
  }, [urlQ]);

  const onChange = (v: string) => {
    setValue(v);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      void navigate({ to: "/library", search: (prev: Record<string, unknown>) => ({ ...(urlQ === null ? {} : prev), q: v || undefined, page: undefined }) });
    }, 150);
  };
  useEffect(() => () => clearTimeout(timer.current), []);

  return (
    <label className="gsearch">
      {icon}
      <input
        id="global-search"
        type="search"
        placeholder="Search papers, passages, authors"
        aria-label="Search documents"
        autoComplete="off"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
      <kbd>/</kbd>
    </label>
  );
}
