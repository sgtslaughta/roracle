import { memo, useMemo, type ComponentProps } from "react";
import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import { isAvailable, type Citation, type CitationOk } from "@/api/chat";
import { openDoc } from "@/features/reader/useReaderStore";
import { remarkCiteMarkers } from "./citeMarkers";

export const openCitation = (c: CitationOk) =>
  openDoc(c.doc_id, { page: c.page, chunkId: c.chunk_id, version: c.version, bbox: c.bbox });

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
  const components = useMemo<Components>(() => ({
    "cite-ref": ({ n: raw }: { n?: string }) => {
      const n = Number(raw);
      const c = citations.find((x) => x.n === n);
      if (!c) {
        if (streaming && !citations.length) return <sup className="c pending" data-n={n}>{n}</sup>;
        return <>[{n}]</>;
      }
      if (!isAvailable(c)) return <sup className="c gone" data-n={n} title="Source not available">{n}</sup>;
      return (
        <sup>
          <button
            type="button" className={`c${hot === n ? " hot" : ""}`} data-n={n}
            aria-label={`Source ${n}: ${c.title}, page ${c.page}`}
            onClick={() => openCitation(c)}
            onMouseEnter={() => onHot?.(n)} onMouseLeave={() => onHot?.(null)}
            onFocus={() => onHot?.(n)} onBlur={() => onHot?.(null)}
          >{n}</button>
        </sup>
      );
    },
    a: ({ node: _node, ...p }) => <a {...p} target="_blank" rel="noreferrer noopener" />,
    table: ({ node: _node, ...p }) => <div className="tbl"><table {...p} /></div>,
  } as Components), [citations, streaming, hot, onHot]);

  return (
    <div className="ans-text">
      <Markdown remarkPlugins={remarkPlugins} rehypePlugins={rehypePlugins} components={components}>{text}</Markdown>
      {streaming && <span className="caret" aria-hidden="true" />}
    </div>
  );
}

export const AnswerText = memo(AnswerTextImpl);
