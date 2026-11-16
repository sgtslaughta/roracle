import { BookOpen, Plus, TextQuote, X } from "lucide-react";
import { OverflowList } from "@/components/OverflowList";
import { useDoc } from "@/features/reader/api";
import { useReaderStore, type ReaderTab } from "@/features/reader/useReaderStore";
import { cn } from "@/lib/utils";
import { setQuote, toggleIgnored, useAskContext, type Quote } from "./useAskContext";

const clip = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1).trimEnd()}…` : s);

function DocChip({ tab, off }: { tab: ReaderTab; off: boolean }) {
  const title = useDoc(tab.docId).data?.title;
  return (
    <span className={cn("cchip", off && "off")} title={title}>
      <BookOpen aria-hidden />
      {`Reading ${title ? clip(title, 28) : "document"}, p. ${tab.page}`}
      <button type="button" onClick={() => toggleIgnored(tab.docId)} aria-label={off ? "Use this document" : "Ignore this document"}>
        {off ? <Plus aria-hidden /> : <X aria-hidden />}
      </button>
    </span>
  );
}

const QuoteChip = ({ quote }: { quote: Quote }) => (
  <span className="cchip qt" title={quote.text}>
    <TextQuote aria-hidden />
    “{clip(quote.text, 60)}”
    <button type="button" onClick={() => setQuote(null)} aria-label="Remove quote"><X aria-hidden /></button>
  </span>
);

type Item = { kind: "tab"; tab: ReaderTab } | { kind: "quote"; quote: Quote };

/** What the next question will be asked against: open reader tabs (toggle to ignore) and a selected passage. */
export function ContextChips() {
  const { tabs } = useReaderStore();
  const { ignored, quote } = useAskContext();
  const items: Item[] = [...tabs.map((tab): Item => ({ kind: "tab", tab })), ...(quote ? [{ kind: "quote", quote } as Item] : [])];
  return (
    <OverflowList
      className="ctx" items={items} max={4} menuLabel="More context" moreLabel={(k) => `+${k} more`}
      renderItem={(it) => it.kind === "tab"
        ? <DocChip key={it.tab.docId} tab={it.tab} off={ignored.includes(it.tab.docId)} />
        : <QuoteChip key="quote" quote={it.quote} />}
    />
  );
}
