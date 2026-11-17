"""check_site: external resources fail, content links and relative refs pass."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_site


class CheckSite(unittest.TestCase):
    def run_on(self, name: str, text: str) -> int:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / name).write_text(text)
            return check_site.main(Path(d))

    def test_content_anchor_and_relative_ok(self):
        html = '<a href="https://example.com/x">x</a><script src="./a.js"></script><link href="a.css">'
        self.assertEqual(self.run_on("a.html", html), 0)

    def test_external_script_fails(self):
        self.assertEqual(self.run_on("a.html", '<script src="https://cdn.example/a.js"></script>'), 1)

    def test_external_link_and_css_fail(self):
        self.assertEqual(self.run_on("a.html", '<link rel="stylesheet" href="//f.example/a.css">'), 1)
        self.assertEqual(self.run_on("a.css", "@import url(https://f.example/a.css);"), 1)
        self.assertEqual(self.run_on("a.css", "a{background:url('http://f.example/x.png')}"), 1)

    def test_js_loader_url_fails_unless_allowed(self):
        self.assertEqual(self.run_on("a.js", 'load("https://evil.example/x.js")'), 1)
        self.assertEqual(self.run_on("a.js", 'load("https://unpkg.com/mermaid@12/dist/mermaid.min.js")'), 0)


if __name__ == "__main__":
    unittest.main()
