"""Run: cd backend && uv run python -m unittest discover -s ../scripts/docs/tests -t ../scripts/docs"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gen_reference as g  # noqa: E402


class DriftTest(unittest.TestCase):
    def test_drift_detection(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            for d in (a, b):
                for f in g.FILES:
                    (Path(d) / f).write_text("x")
            self.assertEqual(g.drift(Path(a), Path(b)), [])
            (Path(b) / "cli.md").write_text("y")
            self.assertEqual(g.drift(Path(a), Path(b)), ["cli.md"])
            (Path(b) / "settings.md").unlink()
            self.assertEqual(g.drift(Path(a), Path(b)), ["settings.md", "cli.md"])

    def test_check_matches_committed_and_is_deterministic(self):
        self.assertEqual(g.main(["--check"]), 0)

    def test_check_fails_on_stale_file(self):
        f = g.OUT / "cli.md"
        orig = f.read_text()
        try:
            f.write_text(orig + "stale\n")
            self.assertEqual(g.main(["--check"]), 1)
        finally:
            f.write_text(orig)


if __name__ == "__main__":
    unittest.main()
