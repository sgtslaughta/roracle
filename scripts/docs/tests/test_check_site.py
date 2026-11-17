"""check_site: external resources fail (incl. obfuscated forms); content links and relative refs pass."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_site

BAD_HTML = [
    '<script src="https://cdn.example/a.js"></script>',
    "<script src=//evil.x/a.js></script>",
    "<link rel=stylesheet href=https://evil.x/a.css>",
    '<script src="&#47;&#47;evil.x/a.js"></script>',
    '<script src=" https://evil.x/a.js"></script>',
    '<script src="\thttps://evil.x/a.js"></script>',
    '<script src="\\\\evil.x/a.js"></script>',
    '<img srcset="a.png 1x, https://evil.x/b.png 2x">',
    "<base href=https://evil.x/>",
    '<meta http-equiv="refresh" content="0; url=https://evil.x/">',
    '<svg><use href="https://evil.x/s.svg#a"></use></svg>',
    '<link rel="stylesheet" href="//f.example/a.css">',
    '<img style="background:url(https://evil.x/a.png)">',
    "<style>@import url(https://evil.x/a.css);</style>",
    '<script>import("https://esm.sh/x")</script>',
    '<script>fetch("//evil.x/a")</script>',
    '<script type="application/json">{"u": "https://evil.x/a.js"}</script>',
    '<iframe src="https://evil.x/"></iframe>',
]
BAD_FILES = {
    "a.css": [
        "@import url(https://f.example/a.css);",
        "a{background:url('http://f.example/x.png')}",
        "@import 'https://f.example/a.css';",
    ],
    "a.js": [
        'load("https://evil.x/x.js")',
        'load("//fonts.googleapis.com/css2?family=X")',
        'import("https://evil.x/m.mjs")',
    ],
    "a.mjs": ['import "https://evil.x/m.mjs"'],
    "a.json": ['{"k": ["https://evil.x/a"]}', '{"k": "//evil.x/a"}'],
    "a.svg": ['<svg xmlns="http://www.w3.org/2000/svg"><image href="https://evil.x/a.png"/></svg>'],
}
OK_HTML = [
    '<a href="https://example.com/x">x</a><script src="./a.js"></script><link href="a.css"><img srcset="a.png 1x, b.png 2x">',
    '<svg xmlns="http://www.w3.org/2000/svg"></svg>',
    '<script>var a = 1; // https is a comment-free word\nvar b = "x";</script>',
    "<p>see https://example.com in prose</p>",
]


def run_on(name: str, text: str) -> int:
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / name).write_text(text)
        return check_site.main(Path(d))


class CheckSite(unittest.TestCase):
    def test_html_probes_fail(self):
        for h in BAD_HTML:
            with self.subTest(h=h):
                self.assertEqual(run_on("a.html", h), 1)

    def test_file_probes_fail(self):
        for name, cases in BAD_FILES.items():
            for c in cases:
                with self.subTest(name=name, c=c):
                    self.assertEqual(run_on(name, c), 1)

    def test_ok_cases_pass(self):
        for h in OK_HTML:
            with self.subTest(h=h):
                self.assertEqual(run_on("a.html", h), 0)

    def test_allowlisted_exact_url_only(self):
        self.assertEqual(run_on("a.js", 'x("https://unpkg.com/mermaid@12/dist/mermaid.min.js")'), 0)
        self.assertEqual(run_on("a.js", 'x("https://unpkg.com/evil@1/e.js")'), 1)

    def test_pinned_mermaid_hash(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "assets/js/mermaid.min.js"
            p.parent.mkdir(parents=True)
            p.write_text("tampered")
            self.assertEqual(check_site.main(Path(d)), 1)


if __name__ == "__main__":
    unittest.main()
