#!/bin/sh
# Assert parser hardening + NetworkPolicy invariants. Usage: assert-render.sh [rendered.yaml] -- reads a pre-rendered
# full-values manifest (needs only python3+PyYAML); otherwise renders ci/full-values.yaml itself (args go to helm template).
set -eu
if [ -f "${1:-}" ]; then f=$(realpath "$1"); shift; fi
cd "$(dirname "$0")/.."
{ if [ -n "${f:-}" ]; then cat "$f"; else helm template r . -f ci/full-values.yaml "$@"; fi; } | python3 -I -c '
import sys, yaml
docs = [d for d in yaml.safe_load_all(sys.stdin) if d]
def need(c, m):
    if not c: sys.exit("assert-render FAIL: " + m)
cm = [d for d in docs if d["kind"] == "ConfigMap" and d["metadata"]["name"].endswith("-clamav-conf")]
need(cm, "clamav ConfigMap missing")
conf = cm[0]["data"]["clamd.conf"].split("\n")
need("AlertExceedsMax yes" in conf, "clamd.conf lacks AlertExceedsMax yes")
lims = {l.split()[0]: l.split()[1] for l in conf if l.startswith(("StreamMaxLength", "MaxFileSize", "MaxScanSize"))}
need(len(lims) == 3, "clamd.conf lacks one of StreamMaxLength/MaxFileSize/MaxScanSize")
mult = {"K": 1 << 10, "M": 1 << 20, "G": 1 << 30}
for k, v in lims.items():
    b = int(v[:-1]) * mult[v[-1].upper()] if v[-1].upper() in mult else int(v)
    need(1073741824 <= b < 4294967296, "%s=%s outside [1G evidence cap, 4G)" % (k, v))
need(not any("User " in l for l in conf), "clamd.conf must not set User")
cl = [d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"].endswith("-clamav")][0]
need(cl["spec"]["template"]["spec"]["containers"][0]["command"] == ["/init-unprivileged"], "clamav must run /init-unprivileged")
mirror = not any(e["name"] == "CLAMAV_NO_FRESHCLAMD" for e in cl["spec"]["template"]["spec"]["containers"][0].get("env", []))
nps = [d for d in docs if d["kind"] == "NetworkPolicy"]
names = {d["metadata"]["name"] for d in nps}
need(any(n.endswith("-default-deny") for n in names), "default-deny missing")
for d in nps:
    n = d["metadata"]["name"]
    if "-parser-" not in n: continue
    s = d["spec"]
    need(s["policyTypes"] == ["Ingress", "Egress"], n + " policyTypes")
    eg = s.get("egress")
    if n.endswith("-clamav") and mirror:
        need(eg and len(eg) == 1 and sorted(p["port"] for p in eg[0]["ports"]) == [80, 443], n + " only mirror egress allowed")
    else:
        need(not eg, n + " must have no egress rules")
for d in nps:
    n = d["metadata"]["name"]
    for r in d["spec"].get("ingress") or []:
        need(r.get("from"), n + ": ingress rule without a non-empty from (would allow all)")
        need(all(r["from"]), n + ": empty ingress peer")
    for r in d["spec"].get("egress") or []:
        need(r.get("to"), n + ": egress rule without a non-empty to (would allow all)")
        need(all(r["to"]), n + ": empty egress peer")
        for t in r["to"]:
            need(t.get("ipBlock") or t.get("namespaceSelector") or t.get("podSelector"), n + ": unrecognised peer")
dg = [d for d in nps if d["metadata"]["name"].endswith("-parser-draftgotenberg")][0]["spec"]
need(dg["ingress"][0]["from"][0]["podSelector"]["matchExpressions"][0]["values"] == ["api"], "draftgotenberg ingress must be from api only")
need([e for e in nps if e["metadata"]["name"].endswith("-api-egress-draft")], "api-egress-draft missing")
dd = [d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"].endswith("-draftgotenberg")][0]["spec"]["template"]["spec"]["containers"][0]
need("--chromium-disable-routes" not in dd["args"], "draft gotenberg must have chromium enabled")
need(dd["securityContext"]["readOnlyRootFilesystem"] and dd["resources"]["limits"], "draft gotenberg hardening")
ing = [d for d in docs if d["kind"] == "ConfigMap" and d["metadata"]["name"].endswith("-env")][0]["data"]
lg = [d for d in nps if d["metadata"]["name"].endswith("-parser-latexbuilder")][0]["spec"]
need(lg["ingress"][0]["from"][0]["podSelector"]["matchExpressions"][0]["values"] == ["api"], "latexbuilder ingress must be from api only")
need("egress" not in lg and "Egress" in lg["policyTypes"], "latexbuilder must have no egress")
need([e for e in nps if e["metadata"]["name"].endswith("-api-egress-latex")], "api-egress-latex missing")
ld = [d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"].endswith("-latexbuilder")][0]["spec"]["template"]["spec"]["containers"][0]
need(ld["securityContext"]["readOnlyRootFilesystem"] and ld["resources"]["limits"], "latexbuilder hardening")
need(ing.get("ROR_LATEX_BUILDER", "").endswith("-latexbuilder:8080"), "latex env wiring")
need(ing.get("ROR_DRAFT_GOTENBERG_URL", "").endswith("-draftgotenberg:3000") and ing.get("ROR_BUILD_CONCURRENCY") == "2", "draft env wiring")
dns = [d for d in nps if d["metadata"]["name"].endswith("-allow-dns")][0]["spec"]["podSelector"]["matchExpressions"][0]
need(dns["operator"] == "NotIn", "allow-dns must use NotIn")
need(set(dns["values"]) == ({"gotenberg", "draftgotenberg", "latexbuilder", "docling", "grobid"} if mirror else {"gotenberg", "draftgotenberg", "latexbuilder", "docling", "grobid", "clamav"}),
     "allow-dns NotIn must cover all parsers (clamav excepted only with a mirror): %s" % dns["values"])
pm = [d for d in docs if d["kind"] == "PodMonitor"]
need(pm, "PodMonitor missing")
need([e["port"] for e in pm[0]["spec"]["podMetricsEndpoints"]] == ["http", "health"], "PodMonitor ports must be http (api), health (worker)")
wk = [d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"].endswith("-worker")][0]["spec"]["template"]["spec"]["containers"][0]
need({p["name"]: p["containerPort"] for p in wk["ports"]}.get("health") == 8001, "worker needs a port named health = worker.healthPort")
for d in docs:
    if d["kind"] == "Deployment":
        for c in d["spec"]["template"]["spec"]["containers"]:
            cp = [p["containerPort"] for p in c.get("ports", [])]
            need(len(cp) == len(set(cp)), "duplicate containerPort in " + d["metadata"]["name"])
pr = [d for d in docs if d["kind"] == "PrometheusRule"]
need(pr, "PrometheusRule missing")
alerts = [r["alert"] for g in pr[0]["spec"]["groups"] for r in g["rules"]]
need(sorted(alerts) == sorted(["RoracleApi5xxHigh", "RoracleAuthFailuresBurst", "RoracleRateLimitedBurst", "RoracleForbiddenBurst", "RoracleJobsFailing", "RoracleQueueBacklog", "RoracleAuditWriteFailures"]), "PrometheusRule alert names: %s" % alerts)
print("assert-render OK: %d NetworkPolicies, clamd limits %s" % (len(nps), lims))
'
