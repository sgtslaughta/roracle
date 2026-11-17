"""Air-gap guard for the built docs site: nothing may be loaded from outside the origin.

- HTML/SVG (html.parser): every attribute value of every tag (srcset candidates and meta refresh included) must not
  be an absolute or protocol-relative URL after unescaping and browser URL normalisation. Only `<a href>` content
  links are exempt, and `xmlns*` namespace declarations. Inline <script>/<style> text and style="" are scanned too.
- CSS: @import / url(). JS/MJS: any URL literal must be on the exact-URL allowlist (site-url-allow.txt).
- JSON: string values that are a URL.
- The vendored mermaid bundle is pinned by sha256 (header of the allowlist), not skipped.
"""

import hashlib
import html
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

ALLOW_FILE = Path(__file__).with_name("site-url-allow.txt")
PINNED = "assets/js/mermaid.min.js"
ABS = re.compile(r"^(?:https?:)?//", re.IGNORECASE)
CSS_REF = re.compile(
    r"""(?:@import\s*(?:url\()?\s*|url\(\s*)["']?\s*(?:https?:)?//""",
    re.IGNORECASE,
)
# https?://... anywhere, or a quoted protocol-relative //host.tld (comments are not preceded by a quote).
JS_URL = re.compile(
    r"""(?:https?://|(?<=["'`])//(?=[A-Za-z0-9-]+\.))[^\s"'`)<>\\,;]*""",
    re.IGNORECASE,
)
SRC_ATTRS_JSON = ("application/json", "application/ld+json", "importmap")


def norm(v: str) -> str:
    """What a browser resolves: unescape, drop tab/newline, trim leading control/space, `\\` == `/`."""
    v = re.sub(r"[\t\n\r]", "", html.unescape(v)).lstrip("\x00-\x20")
    return v.replace("\\", "/")


def is_ext(v: str) -> bool:
    return bool(ABS.match(norm(v)))


def load_allow() -> tuple[str, set[str]]:
    pin, urls = "", set()
    for line in ALLOW_FILE.read_text().splitlines():
        if line.startswith("# sha256 "):
            pin = line.split()[-1]
        elif line.strip() and not line.startswith("#"):
            urls.add(line.strip())
    return pin, urls


def check_css(text: str) -> list[str]:
    return [f"css ref: {m.group(0)}" for m in CSS_REF.finditer(text)]


def check_js(text: str, allow: set[str]) -> list[str]:
    return [f"js url: {u}" for u in JS_URL.findall(text) if u not in allow]


def check_json(text: str) -> list[str]:
    bad: list[str] = []

    def walk(o: object) -> None:
        if isinstance(o, str):
            if is_ext(o):
                bad.append(f"json url: {o}")
        elif isinstance(o, list):
            for i in o:
                walk(i)
        elif isinstance(o, dict):
            for i in o.values():
                walk(i)

    try:
        walk(json.loads(text))
    except ValueError:
        bad.append("json: unparseable")
    return bad


class Scan(HTMLParser):
    def __init__(self, allow: set[str]) -> None:
        super().__init__(convert_charrefs=True)
        self.allow, self.bad = allow, []
        self.stack: list[tuple[str, str]] = []  # (tag, kind) for script/style

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        for k, v in a.items():
            if k.startswith("xmlns") or (tag == "a" and k == "href"):
                continue
            if k == "srcset":
                cands = [c.strip().split()[0] for c in v.split(",") if c.strip()]
                self.bad += [f"<{tag} srcset={c}>" for c in cands if is_ext(c)]
            elif k == "style":
                self.bad += [f"<{tag} style> {m}" for m in check_css(html.unescape(v))]
            elif tag == "meta" and k == "content" and a.get("http-equiv", "").lower() == "refresh":
                m = re.search(r"url\s*=\s*['\"]?\s*(\S[^'\"]*)", v, re.IGNORECASE)
                if m and is_ext(m.group(1)):
                    self.bad.append(f"<meta refresh {m.group(1)}>")
            elif is_ext(v):
                self.bad.append(f"<{tag} {k}={v!r}>")
        if tag in ("script", "style"):
            self.stack.append((tag, a.get("type", "").lower()))

    def handle_endtag(self, tag):
        if self.stack and self.stack[-1][0] == tag:
            self.stack.pop()

    def handle_data(self, data):
        if not self.stack:
            return
        tag, typ = self.stack[-1]
        if tag == "style":
            self.bad += [f"inline style: {m}" for m in check_css(data)]
        elif typ in SRC_ATTRS_JSON:
            self.bad += check_json(data)
        else:
            self.bad += [f"inline script: {m}" for m in check_js(data, self.allow)]


def check_html(text: str, allow: set[str]) -> list[str]:
    s = Scan(allow)
    s.feed(text)
    s.close()
    return s.bad


def scan(suffix: str, text: str, allow: set[str]) -> list[str] | None:
    if suffix in (".html", ".svg"):
        return check_html(text, allow)
    if suffix == ".css":
        return check_css(text)
    if suffix in (".js", ".mjs"):
        return check_js(text, allow)
    if suffix == ".json":
        return check_json(text)
    return None


def main(root: Path) -> int:
    pin, allow = load_allow()
    bad: list[str] = []
    n = 0
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if rel == PINNED and hashlib.sha256(p.read_bytes()).hexdigest() != pin:
            bad.append(f"{rel}: sha256 differs from pin in {ALLOW_FILE.name}")
        text = p.read_text(encoding="utf-8", errors="replace") if p.suffix in SCANNED else ""
        found = scan(p.suffix, text, allow)
        if found is not None:
            n += 1
            bad += [f"{rel}: {m}" for m in found]
    if bad:
        print("external resource references in built site:", *bad[:50], sep="\n  ")
        return 1
    print(f"site check OK ({n} html/svg/css/js/json files, no external resource references)")
    return 0


SCANNED = {".html", ".svg", ".css", ".js", ".mjs", ".json"}

if __name__ == "__main__":
    if sys.argv[1] == "--print-urls":  # regenerate site-url-allow.txt body from the given files
        urls = {u for f in sys.argv[2:] for u in JS_URL.findall(Path(f).read_text(errors="replace"))}
        print(*sorted(urls), sep="\n")
        sys.exit(0)
    sys.exit(main(Path(sys.argv[1])))
