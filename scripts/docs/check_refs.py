"""Cross-check prose in the Markdown docs against the code (settings, routes, CLI, MCP tools, metrics, Terraform, Helm).

Absent reference files are skipped, so checks start biting once the doc that carries them exists.
Intentional mentions of removed things go in scripts/docs/refs-allow.txt (`token` or `file.md|token`).

Run: python scripts/docs/check_refs.py   (exit 1 with file:line on any failure)
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_airgap import missing as airgap_missing
from mdutil import ROOT, lines, load_allow, md_files

# Historic records explain why, not what is true now.
EXCLUDE = ("docs/superpowers/", "docs/prototype/")
ROUTE_EXTRA = {
    "/mcp",
    "/metrics",
    "/.well-known/oauth-protected-resource",
}  # mounted outside the OpenAPI schema
TICKS = re.compile(r"`([^`\n]+)`")
SNAKE = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+")
Hit = tuple[int, str]  # (lineno, token)


def ticks(text: str) -> list[Hit]:
    """Backticked spans outside fences, as (lineno, content)."""
    return [(n, m.group(1)) for n, ln, fence in lines(text) if fence is None for m in TICKS.finditer(ln)]


# ---------------------------------------------------------------- pure checks (text -> bad tokens)
def bad_settings(text: str, known: set[str]) -> list[Hit]:
    out = []
    for n, ln, _ in lines(text):
        for t in re.findall(r"ROR_[A-Z0-9_]+", ln):
            ok = t in known or (t.endswith("_") and any(k.startswith(t) for k in known))  # `ROR_S3_*` prefix
            if not ok:
                out.append((n, t))
    return out


def norm_route(p: str) -> str:
    p = re.sub(r"\{[^}]*\}|<[^>]*>|:[A-Za-z_]\w*", "{}", p.split("?", 1)[0])
    return p.rstrip("/") or "/"


def seg_match(cand: str, tmpl: str) -> bool:
    """Segment-wise match; a `{}` segment in either side matches any single segment (concrete ids in examples)."""
    a, b = cand.split("/"), tmpl.split("/")
    return len(a) == len(b) and all(x == y or "{}" in (x, y) for x, y in zip(a, b, strict=True))


ROUTE_RE = re.compile(r"(?<![\w/.:-])(/(?:api|mcp|healthz|readyz|metrics)\b[^\s`\"'),;]*)")


def bad_routes(text: str, routes: set[str]) -> list[Hit]:
    """Route-looking paths in inline code AND fenced blocks (also after an HTTP verb) must be in openapi.json.

    `/api/...` is the ingress/web-proxy prefix (stripped before the API sees it).
    """
    norm = {norm_route(r) for r in routes} | ROUTE_EXTRA
    spans = ticks(text) + [(n, ln) for n, ln, fence in lines(text) if fence is not None]
    out = []
    for n, s in spans:
        for t in ROUTE_RE.findall(s):
            t = t.rstrip(".:")
            prefix = t.endswith(("/", "*", "…", "..."))
            p = norm_route(t.rstrip("*…."))
            cands = {p, "/" + p.removeprefix("/api").lstrip("/")} if p.startswith("/api") else {p}
            if p == "/api" or any(seg_match(c, r) for c in cands for r in norm):
                continue
            if prefix and any(r.startswith(c.rstrip("/") + "/") or r == c for c in cands for r in norm):
                continue
            out.append((n, t))
    return out


def bad_cli(text: str, cmds: set[str]) -> list[Hit]:
    """`roracle <sub> ...` invocations in inline code and fences must follow the argparse tree.

    cmds are the tree's full names ('roracle api', ...). Matched at span/line start (after `$`, `uv run`,
    `python -m`) and after `-- `, `|`, `&&`, `;`, `$(` (e.g. `kubectl exec x -- roracle migrate`). Other mid-line
    uses (`kubectl -n roracle delete`, `helm upgrade roracle ...`) are resource names and ignored.
    """
    cmds = cmds | {"roracle"}
    spans = [(n, t) for n, t in ticks(text)]
    spans += [(n, ln) for n, ln, fence in lines(text) if fence is not None]
    out = []
    for n, s in spans:
        for m in re.finditer(
            r"(?:^\s*|--\s+|\|\s*|&&\s*|;\s*|\$\(\s*)(?:\$\s+)?(?:uv run\s+)?(?:python3? -m\s+)?roracle((?:\s+[A-Za-z][\w-]*)+)",
            s,
        ):
            cur = "roracle"
            for w in m.group(1).split():
                nxt = f"{cur} {w}"
                if nxt in cmds:
                    cur = nxt
                elif any(c.startswith(cur + " ") for c in cmds):
                    out.append((n, nxt))
                    break
                else:
                    break
    return out


def bad_mcp(text: str, tools: set[str], extra: set[str]) -> list[Hit]:
    """Tool-looking names (`mcp.<x>`, snake_case in headings, bullets or first table cells) must be registered."""
    out = []
    ok = tools | extra
    for n, ln, fence in lines(text):
        if fence is not None:
            continue
        for t in re.findall(r"`mcp\.([a-z0-9_]+)`", ln):
            if t not in tools:
                out.append((n, f"mcp.{t}"))
        m = re.match(r"^\s*(?:#+\s+|[-*]\s+|\|\s*)`([a-z][a-z0-9_]*)`", ln)
        if m and SNAKE.fullmatch(m.group(1)) and m.group(1) not in ok:
            out.append((n, m.group(1)))
    return out


def bad_metrics(text: str, known: set[str]) -> list[Hit]:
    out = []
    for n, t in ticks(text):
        for name in re.findall(r"\broracle_[a-z0-9_]+", t):
            base = re.sub(r"_(bucket|sum|count|created)$", "", name)
            if not {name, base, base + "_total", base.removesuffix("_total")} & known:
                out.append((n, name))
    return out


def bad_tfvars(text: str, variables: set[str]) -> list[Hit]:
    """`var.x` and column-0 keys in tfvars/hcl fences vs the ROOT module's variables*.tf (module variables are not checked)."""
    out = []
    for n, ln, fence in lines(text):
        for t in re.findall(r"\bvar\.([a-z_][a-z0-9_]*)", ln):
            if t not in variables:
                out.append((n, f"var.{t}"))
        if fence in (
            "tfvars",
            "hcl",
        ):  # column-0 `key = value` lines only (indented ones are resource attributes)
            m = re.match(r"^([a-z_][a-z0-9_]*)\s*=", ln)
            if m and m.group(1) not in variables:
                out.append((n, m.group(1)))
    return out


def helm_has(values: object, path: str) -> bool:
    cur = values
    for part in path.split("."):
        if isinstance(cur, dict) and cur:
            if part not in cur:
                return False
            cur = cur[part]
        else:  # empty map / null / list are free-form placeholders; a scalar has nothing below it
            return cur is None or isinstance(cur, (list, dict))
    return True


def bad_helm(text: str, values: dict) -> list[Hit]:
    out = []
    for n, t in ticks(text):
        if re.fullmatch(r"[a-z][A-Za-z0-9]*(\.[A-Za-z][A-Za-z0-9-]*)+", t) and not helm_has(values, t):
            out.append((n, t))
    return out


# ---------------------------------------------------------------- code-side facts
def code_facts(root: Path) -> tuple[dict, list[str]]:
    """Facts from code + generated reference. Returns (facts, missing required sources)."""
    f: dict = {}
    missing: list[str] = []

    def need(path: Path) -> str:
        if not path.is_file():
            missing.append(path.relative_to(root).as_posix())
            return ""
        return path.read_text()

    ref = root / "docs/reference"
    st = need(ref / "settings.md")
    f["settings"] = set(re.findall(r"`(ROR_[A-Z0-9_]+)`", st))
    # web-container env (nginx envsubst) is read by the frontend image rather than Settings
    for web in [*(root / "frontend/nginx").glob("*"), root / "frontend/Dockerfile"]:
        if web.is_file():
            f["settings"].update(re.findall(r"\bROR_[A-Z0-9_]+", web.read_text(errors="ignore")))
    op = need(ref / "openapi.json")
    f["routes"] = set(json.loads(op)["paths"]) if op else set()
    f["cmds"] = set(re.findall(r"^#+ `(roracle[^`]*)`", need(ref / "cli.md"), re.MULTILINE))
    mt = need(ref / "mcp-tools.md")
    f["tools"] = set(re.findall(r"^## `([a-z0-9_]+)`", mt, re.MULTILINE))
    f["mcp_extra"] = set(re.findall(r"`([a-z][a-z0-9_]*)`", mt))  # parameters, prompts, resources
    metrics: set[str] = set()
    for p in list((root / "backend/src").rglob("*.py")) + list((root / "deploy").rglob("*.json")):
        if "node_modules" not in p.parts:
            metrics.update(re.findall(r"\broracle_[a-z0-9_]+", p.read_text(errors="ignore")))
    f["metrics"] = metrics
    tf: set[str] = set()
    for p in (root / "deploy/terraform").glob("variables*.tf"):
        tf.update(re.findall(r'^variable\s+"([^"]+)"', p.read_text(), re.MULTILINE))
    if not tf:
        missing.append("deploy/terraform/variables*.tf")
    f["tfvars"] = tf
    vy = need(root / "deploy/helm/roracle/values.yaml")
    try:
        import yaml
    except ImportError:
        missing.append("PyYAML (python import `yaml`)")
        f["helm"] = {}
    else:
        f["helm"] = yaml.safe_load(vy) or {} if vy else {}
    return f, missing


def run(root: Path = ROOT, allow: set | None = None) -> list[str]:
    facts, missing = code_facts(root)
    if missing:  # required sources: never skip silently
        return [f"required source missing: {m}" for m in missing]
    allow = load_allow(root / "scripts/docs/refs-allow.txt") if allow is None else allow
    errs: list[str] = []
    for p in md_files(root):
        rel = p.relative_to(root).as_posix()
        if rel.startswith(EXCLUDE) or rel in ("docs/reference/settings.md",):
            continue
        text = p.read_text()
        found: list[tuple[str, list[Hit]]] = [
            ("unknown setting", bad_settings(text, facts["settings"])),
            ("route not in openapi.json", bad_routes(text, facts["routes"])),
            ("unknown CLI command", bad_cli(text, facts["cmds"])),
            ("unknown Terraform variable", bad_tfvars(text, facts["tfvars"])),
        ]
        if rel in (
            "docs/reference/mcp.md",
            "docs/enterprise/agent-integration.md",
            "docs/user/agents-and-tokens.md",
        ):
            found.append(("unregistered MCP tool", bad_mcp(text, facts["tools"], facts["mcp_extra"])))
        if rel in ("docs/reference/metrics.md", "docs/enterprise/observability.md"):
            found.append(("unknown metric", bad_metrics(text, facts["metrics"])))
        if rel == "docs/reference/helm-values.md":
            found.append(("key not in values.yaml", bad_helm(text, facts["helm"])))
        for what, hits in found:
            for n, tok in hits:
                if (None, tok) in allow or (rel, tok) in allow:
                    continue
                errs.append(f"{rel}:{n}: {what}: {tok}")
    return errs + airgap_missing(root)


def main() -> int:
    errs = run()
    print("\n".join(errs))
    print(f"check_refs: {len(errs)} problem(s)", file=sys.stderr)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
