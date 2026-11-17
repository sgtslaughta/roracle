import { memo, useEffect, useMemo, useRef, type ComponentProps } from "react";
import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";
import { isAvailable, type Citation, type CitationOk } from "@/api/chat";
import { openDoc } from "@/features/reader/useReaderStore";
import { safeComponents } from "@/components/SafeMarkdown";
import { remarkCiteMarkers } from "./citeMarkers";

export const openCitation = (c: CitationOk) =>
  openDoc(c.doc_id, { page: c.page, chunkId: c.chunk_id ?? undefined, version: c.version ?? undefined, bbox: c.bbox });

export interface AnswerTextProps {
  text: string;
  citations: Citation[];
  streaming?: boolean;
  hot?: number | null;
  onHot?: (n: number | null) => void;
}

const remarkPlugins = [remarkGfm, remarkMath, remarkCiteMarkers];
const rehypePlugins: ComponentProps<typeof Markdown>["rehypePlugins"] = [[rehypeKatex, { throwOnError: false, strict: "ignore" }]];

function AnswerTextImpl({ text, citations, streaming, hot, onHot }: AnswerTextProps) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.querySelectorAll<HTMLElement>("button.c").forEach((b) => b.classList.toggle("hot", Number(b.dataset.n) === hot));
  });

  const components = useMemo<Components>(() => ({
    "cite-ref": ({ n: raw }: { n?: string }) => {
      const n = Number(raw);
      const c = citations.find((x) => x.n === n);
      if (!c) {
        if (streaming && !citations.length) return <sup className="c pending" data-n={n} aria-label={`Source ${n}`}>{n}</sup>;
        return <>[{n}]</>;
      }
      if (!isAvailable(c)) return <sup className="c gone" data-n={n} title="Source not available" aria-label={`Source ${n} not available`}>{n}</sup>;
      return (
        <sup>
          <button
            type="button" className="c" data-n={n}
            aria-label={`Source ${n}: ${c.title}, page ${c.page}`}
            onClick={() => openCitation(c)}
            onMouseEnter={() => onHot?.(n)} onMouseLeave={() => onHot?.(null)}
            onFocus={() => onHot?.(n)} onBlur={() => onHot?.(null)}
          >{n}</button>
        </sup>
      );
    },
    // model output is untrusted: never fetch images (exfiltration via URL), only render safe links
    ...safeComponents("noopener noreferrer"),
  } as Components), [citations, streaming, onHot]);

  return (
    <div className={`ans-text${streaming ? " streaming" : ""}`} ref={ref}>
      <Markdown remarkPlugins={remarkPlugins} rehypePlugins={rehypePlugins} components={components}>{text}</Markdown>
    </div>
  );
}

export const AnswerText = memo(AnswerTextImpl);
