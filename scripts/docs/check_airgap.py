"""Every container image named in `deploy/**` (`image:`, `image =`, `repository:`, `"MIRROR.example.com/..."` literals, the `IMAGES=(` pairs of kind-e2e.sh) must be listed in the air-gap manifest.

The manifest is `docs/enterprise/airgap-mirror-manifest.md`; an image is listed when its repository name (no tag, no
digest) appears in backticks there. Registry placeholders (`MIRROR.example.com/`, `${REGISTRY_MIRROR:-}`,
`localhost:5055/`) and a leading `docker.io/` are ignored. Chart repositories (`oci://`) and variables are skipped.
"""

from __future__ import annotations

import re
from pathlib import Path

MANIFEST = "docs/enterprise/airgap-mirror-manifest.md"
SKIP_DIRS = {".terraform", "node_modules"}
SUFFIXES = {".yaml", ".yml", ".tf", ".tfvars", ".sh"}
STRIP = re.compile(r"\$\{[^}]*\}|MIRROR\.example\.com/|localhost:5055/|^docker\.io/")
REF = re.compile(r"""(?<![\w-])(?:image|repository)["']?\s*[:=]\s*["']?([A-Za-z0-9_.@/:-]+)""", re.IGNORECASE)
PLACEHOLDER = re.compile(r"\"MIRROR\.example\.com/([A-Za-z0-9_.@/:-]+)\"")  # image literals in .tf defaults
PAIR = re.compile(
    r'^\s*"([a-z0-9.-]+(?:/[\w.-]+)+:[\w.-]+) [\w./:-]+"'
)  # kind-e2e.sh IMAGES=( "<upstream> <mirror path>" )
TICK = re.compile(r"`([^`\n]+)`")


def norm(ref: str) -> str:
    """Repository name of an image reference: placeholders, digest and tag removed."""
    ref = STRIP.sub("", ref.strip()).split("@", 1)[0]
    head, sep, tag = ref.rpartition(":")
    return head if sep and "/" not in tag else ref


def image_refs(text: str) -> list[tuple[int, str]]:
    """(lineno, repository name) for each image reference in one file; variables and chart repositories are skipped."""
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        names = [PLACEHOLDER.search(line), PAIR.match(line)]
        out += [(n, norm(m.group(1))) for m in names if m]
        line = STRIP.sub("", line)
        for m in REF.finditer(line):
            v = m.group(1)
            if re.match(r"(oci|https?):", v) or not re.search(r"[/:]", v) or v.endswith(("/", ":")):
                continue
            name = norm(v)
            if name and not name.startswith("."):
                out.append((n, name))
    return out


def manifest_names(text: str) -> set[str]:
    """Repository names (tag and digest removed) of every backticked span in the manifest."""
    return {norm(t) for t in TICK.findall(text)}


def missing(root: Path) -> list[str]:
    """`file:line: image not in the air-gap manifest: name`, empty when the manifest or deploy/ is absent."""
    manifest, deploy = root / MANIFEST, root / "deploy"
    if not manifest.is_file() or not deploy.is_dir():
        return []
    known = manifest_names(manifest.read_text())
    errs = []
    for p in sorted(deploy.rglob("*")):
        rel = p.relative_to(root)
        if (
            not p.is_file()
            or p.suffix not in SUFFIXES
            or SKIP_DIRS & set(rel.parts)
            or "templates" in rel.parts
        ):
            continue
        for n, name in image_refs(p.read_text(errors="ignore")):
            if name not in known:
                errs.append(f"{rel.as_posix()}:{n}: image not in the air-gap manifest: {name}")
    return errs
