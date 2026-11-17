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


def bad_routes(text: str, routes: set[str]) -> list[Hit]:
    """`/api/...` is the ingress/web-proxy prefix (stripped before the API sees it)."""
    norm = {norm_route(r) for r in routes} | ROUTE_EXTRA
    out = []
    for n, t in ticks(text):
        if not re.match(r"/(api|mcp|healthz|readyz|metrics)\b", t) or " " in t:
            continue
        prefix = t.endswith(("/", "*", "…", "..."))
        p = norm_route(t.rstrip("*…."))
        cands = {p, "/" + p.removeprefix("/api").lstrip("/")} if p.startswith("/api") else {p}
        cands = {c if c != "/" else "/" for c in cands}
        if p == "/api" or any(c in norm for c in cands):
            continue
        if prefix and any(r.startswith(c.rstrip("/") + "/") or r == c for c in cands for r in norm):
            continue
        out.append((n, t))
    return out


def bad_cli(text: str, cmds: set[str]) -> list[Hit]:
    """`roracle <sub> ...` at the start of an inline span or fenced line must follow the argparse tree.

    cmds are the tree's full names ('roracle api', ...). Mid-line mentions (kubectl -n roracle ...) are ignored.
    """
    cmds = cmds | {"roracle"}
    spans = [(n, t) for n, t in ticks(text)]
    spans += [(n, ln) for n, ln, fence in lines(text) if fence is not None]
    out = []
    for n, s in spans:
        for m in re.finditer(
            r"^\s*(?:\$\s+)?(?:uv run\s+)?(?:python3? -m\s+)?roracle((?:\s+[A-Za-z][\w-]*)+)", s
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
    out = []
    for n, ln, fence in lines(text):
        for t in re.findall(r"\bvar\.([a-z_][a-z0-9_]*)", ln):
            if t not in variables:
                out.append((n, f"var.{t}"))
        if fence in ("tfvars", "hcl", "terraform"):
            m = re.match(r"^([a-z_][a-z0-9_]*)\s*=", ln)
            if m and fence == "tfvars" and m.group(1) not in variables:
                out.append((n, m.group(1)))
    return out


def helm_has(values: object, path: str) -> bool:
    cur = values
    for part in path.split("."):
        if isinstance(cur, dict) and cur:  # an empty map is free-form (extraEnv, annotations, ...)
            if part not in cur:
                return False
            cur = cur[part]
        else:
            return True  # free-form / list / scalar beyond this point: cannot verify deeper
    return True


def bad_helm(text: str, values: dict) -> list[Hit]:
    out = []
    for n, t in ticks(text):
        if re.fullmatch(r"[a-z][A-Za-z0-9]*(\.[A-Za-z][A-Za-z0-9-]*)+", t) and not helm_has(values, t):
            out.append((n, t))
    return out


# ---------------------------------------------------------------- code-side facts
def code_facts(root: Path) -> dict:
    f: dict = {}
    ref = root / "docs/reference"
    st = ref / "settings.md"
    f["settings"] = set(re.findall(r"`(ROR_[A-Z0-9_]+)`", st.read_text())) if st.is_file() else None
    if (
        f["settings"] is not None
    ):  # web-container env (nginx envsubst), read by the frontend image rather than Settings
        for web in [*(root / "frontend/nginx").glob("*"), root / "frontend/Dockerfile"]:
            if web.is_file():
                f["settings"].update(re.findall(r"\bROR_[A-Z0-9_]+", web.read_text(errors="ignore")))
    op = ref / "openapi.json"
    f["routes"] = set(json.loads(op.read_text())["paths"]) if op.is_file() else None
    cli = ref / "cli.md"
    f["cmds"] = (
        set(re.findall(r"^#+ `(roracle[^`]*)`", cli.read_text(), re.MULTILINE)) if cli.is_file() else None
    )
    mt = ref / "mcp-tools.md"
    if mt.is_file():
        s = mt.read_text()
        f["tools"] = set(re.findall(r"^## `([a-z0-9_]+)`", s, re.MULTILINE))
        f["mcp_extra"] = set(re.findall(r"`([a-z][a-z0-9_]*)`", s))  # parameters, prompts, resources
    else:
        f["tools"] = f["mcp_extra"] = None
    metrics: set[str] = set()
    for p in list((root / "backend/src").rglob("*.py")) + list((root / "deploy").rglob("*.json")):
        if "node_modules" not in p.parts:
            metrics.update(re.findall(r"\broracle_[a-z0-9_]+", p.read_text(errors="ignore")))
    f["metrics"] = metrics
    tf: set[str] = set()
    for p in (root / "deploy/terraform").glob("variables*.tf"):
        tf.update(re.findall(r'^variable\s+"([^"]+)"', p.read_text(), re.MULTILINE))
    f["tfvars"] = tf
    vy = root / "deploy/helm/roracle/values.yaml"
    try:
        import yaml

        f["helm"] = yaml.safe_load(vy.read_text()) if vy.is_file() else None
    except ImportError:
        f["helm"] = None
    return f


def run(root: Path = ROOT, allow: set | None = None) -> list[str]:
    facts = code_facts(root)
    allow = load_allow(root / "scripts/docs/refs-allow.txt") if allow is None else allow
    errs: list[str] = []
    for p in md_files(root):
        rel = p.relative_to(root).as_posix()
        if rel.startswith(EXCLUDE) or rel in ("docs/reference/settings.md",):
            continue
        text = p.read_text()
        found: list[tuple[str, list[Hit]]] = []
        if facts["settings"] is not None:
            found.append(("unknown setting", bad_settings(text, facts["settings"])))
        if facts["routes"] is not None:
            found.append(("route not in openapi.json", bad_routes(text, facts["routes"])))
        if facts["cmds"] is not None:
            found.append(("unknown CLI command", bad_cli(text, facts["cmds"])))
        if facts["tools"] is not None and rel in (
            "docs/reference/mcp.md",
            "docs/enterprise/agent-integration.md",
            "docs/user/agents-and-tokens.md",
        ):
            found.append(("unregistered MCP tool", bad_mcp(text, facts["tools"], facts["mcp_extra"])))
        if rel in ("docs/reference/metrics.md", "docs/enterprise/observability.md"):
            found.append(("unknown metric", bad_metrics(text, facts["metrics"])))
        if facts["tfvars"]:
            found.append(("unknown Terraform variable", bad_tfvars(text, facts["tfvars"])))
        if rel == "docs/reference/helm-values.md" and facts["helm"]:
            found.append(("key not in values.yaml", bad_helm(text, facts["helm"])))
        for what, hits in found:
            for n, tok in hits:
                if (None, tok) in allow or (rel, tok) in allow:
                    continue
                errs.append(f"{rel}:{n}: {what}: {tok}")
    return errs


def main() -> int:
    errs = run()
    print("\n".join(errs))
    print(f"check_refs: {len(errs)} problem(s)", file=sys.stderr)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
