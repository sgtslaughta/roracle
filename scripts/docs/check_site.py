"""Air-gap guard for the built docs site: no external http(s) resource references.

HTML: any src/href(link)/poster/srcset/action attribute, <link>/<script>/<img>/<iframe> targets, @import/url() in
inline styles, must be relative (content `<a href="https://...">` is allowed). CSS: @import / url(). JS: external
loader URLs (.js/.css/font/json) in the shipped bundle must be on JS_ALLOW (optional features we do not enable);
vendored third-party bundles (assets/js/*.min.js) are skipped except for the same CDN-host scan being reported.
"""

import re
import sys
from pathlib import Path

EXT = re.compile(r"^(https?:)?//", re.IGNORECASE)
TAG_ATTR = re.compile(
    r"<(link|script|img|iframe|source|video|audio|embed|object|form)\b([^>]*)>", re.IGNORECASE | re.DOTALL
)
ATTR = re.compile(r"\b(src|href|poster|srcset|action|data)\s*=\s*(?:\"([^\"]*)\"|'([^']*)')", re.IGNORECASE)
CSS_REF = re.compile(
    r"""(?:@import\s+(?:url\()?\s*["']?|url\(\s*["']?)(https?:)?//[^"')\s]+""", re.IGNORECASE
)
JS_URL = re.compile(
    r"""https?://[^"'`\s)\\]+\.(?:js|css|woff2?|ttf|json|wasm)(?=["'`\s)\\]|$)""", re.IGNORECASE
)
# Zensical bundle: loaders for optional features (glightbox, ace, pyodide, polyfills) and mermaid fallback.
# All are dead code here: the features are not enabled, and vendored mermaid defines the global first.
JS_ALLOW = ("https://unpkg.com/", "https://cdn.jsdelivr.net/pyodide/")


def check_html(p: Path, text: str) -> list[str]:
    bad = []
    for tag, attrs in TAG_ATTR.findall(text):
        for name, v1, v2 in ATTR.findall(attrs):
            v = v1 or v2
            if tag.lower() == "link" and name.lower() == "href" and 'rel="canonical"' in attrs:
                continue  # self-reference metadata, not a fetch
            if EXT.match(v) and not (tag.lower() == "form" and name.lower() == "data"):
                bad.append(f'{p}: <{tag} {name}="{v}">')
    bad += [
        f"{p}: {m.group(0)}" for m in CSS_REF.finditer(text) if "(" in m.group(0) or "@import" in m.group(0)
    ]
    return bad


def main(root: Path) -> int:
    bad: list[str] = []
    n = 0
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix not in {".html", ".css", ".js"}:
            continue
        n += 1
        text = p.read_text(encoding="utf-8", errors="replace")
        rel = p.relative_to(root)
        if p.suffix == ".html":
            bad += check_html(rel, text)
        elif p.suffix == ".css":
            bad += [f"{rel}: {m.group(0)}" for m in CSS_REF.finditer(text)]
        elif rel.parts[:2] != ("assets", "js") or "mermaid" not in p.name:
            bad += [f"{rel}: {u}" for u in JS_URL.findall(text) if not u.startswith(JS_ALLOW)]
    if bad:
        print("external resource references in built site:", *bad[:50], sep="\n  ")
        return 1
    print(f"site check OK ({n} html/css/js files, no external resource references)")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
