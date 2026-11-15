import { ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";

export function Pager({ page, pages, onPage }: { page: number; pages: number; onPage: (p: number) => void }) {
  if (pages <= 1) return null;
  return (
    <nav className="pager" aria-label="Pages">
      <Button disabled={page < 2} onClick={() => onPage(page - 1)}><ChevronLeft aria-hidden />Previous</Button>
      {Array.from({ length: pages }, (_, i) => (
        <Button key={i} aria-current={page === i + 1 ? "true" : undefined} onClick={() => onPage(i + 1)}>{i + 1}</Button>
      ))}
      <Button disabled={page >= pages} onClick={() => onPage(page + 1)}>Next<ChevronRight aria-hidden /></Button>
    </nav>
  );
}
