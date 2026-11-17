import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_airgap as ca


class ImageRefTest(unittest.TestCase):
    def test_norm(self):
        self.assertEqual(ca.norm("registry:2.8.3@sha256:abc"), "registry")
        self.assertEqual(ca.norm("localhost:5055/bitnamilegacy/minio:2025.5.24"), "bitnamilegacy/minio")
        self.assertEqual(ca.norm("${REGISTRY_MIRROR:-}postgres:16"), "postgres")
        self.assertEqual(ca.norm("docker.io/rook/ceph:v1.18.0"), "rook/ceph")
        self.assertEqual(ca.norm("registry.example:5000/a/b"), "registry.example:5000/a/b")

    def test_yaml_and_compose(self):
        text = (
            "image: ${REGISTRY_MIRROR:-}postgres:16\n"
            "  image: {repository: gotenberg/gotenberg, tag: 8.20.1, digest: ''}\n"
            "image: {repository: MIRROR.example.com/otel/collector}\n"
            "# image: commented/out:1\n"
        )
        self.assertEqual(
            [n for _, n in ca.image_refs(text)], ["postgres", "gotenberg/gotenberg", "otel/collector"]
        )

    def test_skips_variables_charts_and_types(self):
        text = (
            "image: $img\n"
            "repository = optional(string)\n"
            'repository = "oci://registry.example/charts"\n'
            "image = var.image\n"
            'image: {{ include "x" . }}\n'
        )
        self.assertEqual(ca.image_refs(text), [])

    def test_terraform_default_and_kind_pairs(self):
        text = (
            '  image = optional(string, "MIRROR.example.com/vllm/vllm-openai:0.0.0-PLACEHOLDER")\n'
            '  "ghcr.io/cloudnative-pg/cloudnative-pg:1.26.0 cloudnative-pg/cloudnative-pg:1.26.0"\n'
        )
        self.assertEqual(
            [n for _, n in ca.image_refs(text)], ["vllm/vllm-openai", "ghcr.io/cloudnative-pg/cloudnative-pg"]
        )

    def test_ceph_locals_and_registry_assignment(self):
        tf = (
            "  image_defaults = {\n"
            '    rook_ceph    = "rook/ceph" # operator image\n'
            '    cnpg         = "cloudnative-pg/cloudnative-pg"\n'
            "  }\n"
            '  other = "not/an-image"\n'
            '  repository = coalesce(var.x, "${var.mirror_registry}/ceph/ceph")\n'
        )
        self.assertEqual(
            [n for _, n in ca.image_refs(tf)], ["rook/ceph", "cloudnative-pg/cloudnative-pg", "ceph/ceph"]
        )
        sh = "REGISTRY_IMAGE=registry:2.8.3@sha256:abc\n"
        self.assertEqual([n for _, n in ca.image_refs(sh)], ["registry"])

    def test_manifest_names_table_rows_only(self):
        names = ca.manifest_names("| `a/b` | `1` |\nprose mentions `c/d` only\n")
        self.assertIn("a/b", names)
        self.assertNotIn("c/d", names)

    def test_listed_allows_registry_prefix(self):
        self.assertTrue(ca.listed("ceph/ceph", {"quay.io/ceph/ceph"}))
        self.assertFalse(ca.listed("ceph", {"quay.io/ceph/ceph"}))

    def test_manifest_names(self):
        names = ca.manifest_names("| `a/b` | `1.2` |\n`c/d:3@sha256:ff` and `docker.io/e/f:1`\n")
        self.assertTrue({"a/b", "c/d", "e/f", "1.2"} <= names)


class MissingTest(unittest.TestCase):
    def _tree(self, root: Path, manifest: str | None):
        (root / "deploy/helm/roracle/templates").mkdir(parents=True)
        (root / "deploy/helm/roracle/values.yaml").write_text("image: {repository: acme/app, tag: '1'}\n")
        (root / "deploy/helm/roracle/templates/x.yaml").write_text("image: {{ .x }}/ignored:1\n")
        (root / "deploy/.terraform").mkdir()
        (root / "deploy/.terraform/p.tf").write_text('image = "bad/skipped:1"\n')
        if manifest is not None:
            (root / "docs/enterprise").mkdir(parents=True)
            (root / ca.MANIFEST).write_text(manifest)

    def test_reports_unlisted_image(self):
        with tempfile.TemporaryDirectory() as d:
            r = Path(d)
            self._tree(r, "| `other/thing` | `1` |\n")
            self.assertEqual(
                ca.missing(r),
                ["deploy/helm/roracle/values.yaml:1: image not in the air-gap manifest: acme/app"],
            )

    def test_listed_image_passes(self):
        with tempfile.TemporaryDirectory() as d:
            r = Path(d)
            self._tree(r, "| `acme/app` | `1` | not pinned in repo |\n")
            self.assertEqual(ca.missing(r), [])

    def test_absent_manifest_is_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            r = Path(d)
            self._tree(r, None)
            self.assertEqual(ca.missing(r), [])


if __name__ == "__main__":
    unittest.main()
