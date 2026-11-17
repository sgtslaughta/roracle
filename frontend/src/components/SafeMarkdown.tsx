import { lazy, Suspense } from "react";
import type { Components } from "react-markdown";

/** Allow only same-site relative paths, fragments and absolute http(s) URLs. */
export function safeHref(href: string | undefined): { href: string; external: boolean } | null {
  if (!href) return null;
  const ok = (href.startsWith("/") && !href.startsWith("//")) || href.startsWith("#") || /^https?:\/\//i.test(href);
  if (!ok) return null;
  if (href.startsWith("#")) return { href, external: false }; // in-page fragment: cannot change host
  try {
    const u = new URL(href, location.origin);
    if (u.protocol !== "http:" && u.protocol !== "https:") return null;
    if (!/^https?:\/\//i.test(href) && u.origin !== location.origin) return null; // relative must stay relative (e.g. "/\\evil.com")
    // always emit the absolute validated URL: a same-origin pathname like "//evil.com" would be protocol-relative
    return { href: u.href, external: u.origin !== location.origin };
  } catch { return null; }
}

/** Element overrides for untrusted Markdown: images never load (alt text only), links only via `safeHref`, tables scroll. */
export function safeComponents(rel = "noopener noreferrer nofollow"): Components {
  return {
    img: ({ alt }) => <>{alt ?? ""}</>,
    a: ({ node: _node, href, children }) => {
      const s = safeHref(href);
      if (!s) return <span>{children}</span>;
      return s.external
        ? <a href={s.href} target="_blank" rel={rel}>{children}</a>
        : <a href={s.href}>{children}</a>;
    },
    table: ({ node: _node, ...p }) => <div className="tbl"><table {...p} /></div>,
  };
}

const components = safeComponents();
const MarkdownBody = lazy(() => import("./MarkdownBody"));

/** User-authored Markdown (note bodies): GFM only, no raw HTML, no math, no images. The renderer loads on demand; meanwhile the plain text shows. */
export function SafeMarkdown({ text, className }: { text: string; className?: string }) {
  return (
    <div className={className ? `md ${className}` : "md"}>
      <Suspense fallback={<p data-md-fallback style={{ whiteSpace: "pre-wrap" }}>{text}</p>}>
        <MarkdownBody text={text} components={components} />
      </Suspense>
    </div>
  );
}
