import { ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";

/** 1 … p-1 p p+1 … last: never more than 7 numbered entries however many pages there are. */
export function pageItems(page: number, pages: number): (number | "gap-l" | "gap-r")[] {
  if (pages <= 7) return Array.from({ length: pages }, (_, i) => i + 1);
  const set = new Set([1, pages, page - 1, page, page + 1].filter((n) => n >= 1 && n <= pages));
  const nums = [...set].sort((a, b) => a - b);
  const out: (number | "gap-l" | "gap-r")[] = [];
  nums.forEach((n, i) => {
    if (i && n - nums[i - 1] > 1) out.push(i === 1 ? "gap-l" : "gap-r");
    out.push(n);
  });
  return out;
}

export function Pager({ page, pages, onPage }: { page: number; pages: number; onPage: (p: number) => void }) {
  if (pages <= 1) return null;
  return (
    <nav className="pager" aria-label="Pages">
      <Button disabled={page < 2} onClick={() => onPage(page - 1)}><ChevronLeft aria-hidden />Previous</Button>
      {pageItems(page, pages).map((p) =>
        typeof p === "string"
          ? <span key={p} className="gap" aria-hidden>…</span>
          : <Button key={p} aria-current={page === p ? "true" : undefined} aria-label={`Page ${p}`} onClick={() => onPage(p)}>{p}</Button>,
      )}
      <Button disabled={page >= pages} onClick={() => onPage(page + 1)}>Next<ChevronRight aria-hidden /></Button>
    </nav>
  );
}
