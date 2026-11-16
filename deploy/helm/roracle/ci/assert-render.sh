#!/bin/sh
# Render ci/full-values.yaml and assert parser hardening + NetworkPolicy invariants. Needs helm and python3 (PyYAML).
set -eu
cd "$(dirname "$0")/.."
helm template r . -f ci/full-values.yaml | python3 -I -c '
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
nps = [d for d in docs if d["kind"] == "NetworkPolicy"]
names = {d["metadata"]["name"] for d in nps}
need(any(n.endswith("-default-deny") for n in names), "default-deny missing")
for d in nps:
    n = d["metadata"]["name"]
    if "-parser-" not in n: continue
    s = d["spec"]
    need(s["policyTypes"] == ["Ingress", "Egress"], n + " policyTypes")
    eg = s.get("egress")
    if n.endswith("-clamav"):
        need(eg and len(eg) == 1 and sorted(p["port"] for p in eg[0]["ports"]) == [80, 443], n + " only mirror egress allowed")
    else:
        need(not eg, n + " must have no egress rules")
print("assert-render OK: %d NetworkPolicies, clamd limits %s" % (len(nps), lims))
'
