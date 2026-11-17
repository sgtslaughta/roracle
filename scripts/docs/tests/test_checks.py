"""Unit tests for check_links / check_refs core functions. Run via scripts/docs/check.sh."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_links as cl
import check_refs as cr
import mdutil


class LinkTest(unittest.TestCase):
    def test_slug(self):
        self.assertEqual(cl.slug("Foo `bar` & Baz: 1.2"), "foo-bar-baz-12")
        self.assertEqual(cl.slug("[Link](x.md) *it*"), "link-it")

    def test_anchors_dupes_and_fences(self):
        a = cl.anchors('# A\n## A\n```\n# hidden\n```\n<a name="x"></a>\n')
        self.assertEqual(a, {"a", "a-1", "x"})

    def test_targets_skip_code(self):
        t = cl.targets("[a](b.md) `[c](d.md)`\n```\n[e](f.md)\n```\n[r]: g.md\n")
        self.assertEqual([x[1] for x in t], ["b.md", "g.md"])

    def test_check_dir(self):
        with tempfile.TemporaryDirectory() as d:
            r = Path(d)
            (r / "docs").mkdir()
            (r / "docs/a.md").write_text(
                "# T\n[ok](b.md#hello) [bad](b.md#nope) [gone](c.md) [self](#t) [web](https://x.example/p)\n"
                "[badweb](https://) [abs](/docs/b.md)\n"
            )
            (r / "docs/b.md").write_text("## Hello\n")
            errs = cl.check(r, [r / "docs/a.md"])
            self.assertEqual(len(errs), 3, errs)
            self.assertTrue(errs[0].startswith("docs/a.md:2: broken anchor"))
            self.assertIn("docs/a.md:2: broken link", errs[1])
            self.assertIn("malformed URL", errs[2])


class RefsTest(unittest.TestCase):
    def test_settings(self):
        k = {"ROR_A", "ROR_S3_X"}
        self.assertEqual(cr.bad_settings("ROR_A ROR_B ROR_S3_*\n", k), [(1, "ROR_B")])

    def test_routes(self):
        routes = {"/documents/{doc_id}", "/healthz"}
        txt = "`/api/documents/{id}` `/api/documents/` `/healthz` `/mcp` `/api/nope` `/metrics` `/api` `/api/doc/x`"
        self.assertEqual([t for _, t in cr.bad_routes(txt, routes)], ["/api/nope", "/api/doc/x"])

    def test_cli(self):
        cmds = {"roracle api", "roracle perf", "roracle perf suite"}
        txt = "`roracle perf suite --x` `roracle bogus`\n```sh\n$ roracle perf nope\nkubectl -n roracle delete x\n```\n"
        self.assertEqual([t for _, t in cr.bad_cli(txt, cmds)], ["roracle bogus", "roracle perf nope"])

    def test_mcp(self):
        txt = "## `search`\n| `get_doc` | x |\n- `max_label` y\n`mcp.ask` `mcp.zzz`\n"
        got = [t for _, t in cr.bad_mcp(txt, {"search", "ask"}, {"max_label"})]
        self.assertEqual(got, ["get_doc", "mcp.zzz"])

    def test_metrics(self):
        k = {"roracle_jobs", "roracle_http_requests_total"}
        txt = "`roracle_http_requests_total` `roracle_jobs_bucket` `roracle_nope_seconds`"
        self.assertEqual(cr.bad_metrics(txt, k), [(1, "roracle_nope_seconds")])

    def test_tfvars(self):
        txt = "use var.known and var.gone\n```tfvars\nknown = 1\nstale = 2\n```\n"
        self.assertEqual([t for _, t in cr.bad_tfvars(txt, {"known"})], ["var.gone", "stale"])

    def test_helm(self):
        v = {"api": {"replicas": 2, "extra": {}}, "list": [1]}
        txt = "`api.replicas` `api.extra.anything` `api.nope` `list.x.y` `plain`"
        self.assertEqual(cr.bad_helm(txt, v), [(1, "api.nope")])

    def test_allow_list(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.txt"
            p.write_text("# c\nfoo  # why\ndocs/x.md|bar\n")
            self.assertEqual(mdutil.load_allow(p), {(None, "foo"), ("docs/x.md", "bar")})


if __name__ == "__main__":
    unittest.main()
