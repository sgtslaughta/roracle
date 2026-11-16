"""Regenerates fixture.yaml: `python tests/eval/gen_fixture.py tests/eval/fixture.yaml` (then edit freely)."""

import sys
from pathlib import Path

import yaml

SIG = [
    "perovskite",
    "tardigrade",
    "magnetar",
    "stromatolite",
    "halophile",
    "quasicrystal",
    "ferrofluid",
    "kelvinon",
    "osmolyte",
    "pulsar",
    "cryoprobe",
    "diatomite",
    "brachiopod",
    "lithophyte",
    "spintronic",
    "zeolite",
    "aerogel",
    "xenolith",
    "plasmonic",
    "archaeon",
    "bolometer",
    "cenote",
    "dendrimer",
    "exciton",
    "fumarole",
    "geode",
    "hydrogel",
    "ionogel",
    "jarosite",
    "kimberlite",
    "lysimeter",
    "mudstone",
]
FACT = [
    "tensile",
    "birefringence",
    "coercivity",
    "luminosity",
    "viscosity",
    "porosity",
    "mobility",
    "salinity",
    "turbidity",
    "opacity",
    "ductility",
    "fluorescence",
    "conductance",
    "remanence",
    "permittivity",
    "hysteresis",
    "elasticity",
    "reflectance",
    "albedo",
    "capacitance",
    "susceptibility",
    "brittleness",
    "absorbance",
    "resistivity",
    "solubility",
    "hardness",
    "tortuosity",
    "buoyancy",
    "refractivity",
    "density",
    "attenuation",
    "dispersion",
]
THEME = "calibration shielding annealing sampling titration drift triangulation hysteresis_loop".replace(
    "hysteresis_loop", "normalisation"
).split()
assert len(SIG) == 32 and len(FACT) == 32 and len(set(SIG + FACT + THEME)) == 72
P = {"alice": ["/eng"], "bob": ["/sales"], "carol": [], "root": []}
docs = []
for i in range(32):
    d = {"key": f"d{i:02d}", "title": f"{SIG[i].capitalize()} report {i}"}
    if i < 6:
        own, vis = ["root", "alice", "bob", "carol", "root", "root"][i], "everyone"
    elif i < 12:
        own, vis = ["alice", "root"][i % 2], "org_unit"
        d["org_unit"] = "/eng"
    elif i < 18:
        own, vis = ["bob", "root"][i % 2], "org_unit"
        d["org_unit"] = "/sales"
    elif i < 24:
        own, vis = "root", "shared"
        d["shares"] = {
            18: ["bob"],
            19: ["carol"],
            20: ["alice", "bob"],
            21: ["carol"],
            22: ["bob", "carol"],
            23: ["alice"],
        }[i]
    else:
        own, vis = ["root", "alice", "bob", "carol"][i % 4], "private"
    d["owner"], d["visibility"] = own, vis
    if i == 5:
        d["label"] = "Secret"
    if i == 4:
        d["label"] = "Internal"
    s, f, t = SIG[i], FACT[i], THEME[i % 8]
    d["chunks"] = [
        {
            "text": f"{s.capitalize()} characterisation. The {s} sample was measured at {200 + i} kelvin and reported a {f} anomaly of {i + 3} percent. The measurements were repeated three times and averaged.",
            "headings": [f"{s.capitalize()} study"],
            "pages": [1],
        },
        {
            "text": f"Follow-up work on {s} used {t} protocols. The {s} {t} review confirmed the {f} result and recommended further tests.",
            "headings": [f"{s.capitalize()} follow-up"],
            "pages": [2],
        },
    ]
    docs.append(d)
D = {d["key"]: d for d in docs}


def sees(p, d, chat=True):
    lab = d.get("label")
    if chat and lab == "Secret":
        return False
    if p == "root":
        return True
    if d["owner"] == p:
        return True
    v = d["visibility"]
    if v == "everyone":
        return True
    if v == "shared":
        return p in d.get("shares", [])
    if v == "org_unit":
        return d["org_unit"] in P[p]
    return False


NON = ["alice", "bob", "carol"]


def pick(keys, base=("root",)):
    """principals: root-free: at least one seeing something and one not seeing something."""
    for combo in [("alice", "bob"), ("bob", "carol"), ("alice", "carol"), ("alice", "bob", "carol")]:
        miss = any(not sees(p, D[k]) for p in combo for k in keys)
        hit = any(sees(p, D[k]) for p in combo for k in keys)
        if miss and hit:
            return list(combo)
    for combo in [("root", "carol"), ("root", "alice"), ("root", "bob")]:
        if any(not sees(p, D[k]) for p in combo for k in keys):
            return list(combo)
    return None


queries = []
fact_docs = [f"d{i:02d}" for i in list(range(6, 32)) if i not in (13, 29)]  # 24


def q(key, text, kind, keys, rel, as_pat=False):
    pr = pick(keys)
    assert pr, key
    queries.append(
        {"key": key, "text": text, "kind": kind, "principals": pr, "as_pat": as_pat, "relevant": rel}
    )


pat_fact = {"d06", "d07", "d19", "d24", "d25"}
for n, k in enumerate(fact_docs):
    i = int(k[1:])
    q(
        f"f{n:02d}",
        f"What anomaly did the {SIG[i]} study report for {FACT[i]}?",
        "fact",
        [k],
        [{"doc": k, "quote": f"{FACT[i]} anomaly", "grade": 3}],
        as_pat=(k in pat_fact or n == 0),
    )
# the first query keeps as_pat (loader tests); put Secret/Internal docs in via multihop/global
mh = [
    ("d04", "d08"),
    ("d05", "d09"),
    ("d10", "d20"),
    ("d11", "d22"),
    ("d14", "d21"),
    ("d15", "d26"),
    ("d16", "d27"),
    ("d12", "d18"),
]
for n, (a, b) in enumerate(mh):
    ia, ib = int(a[1:]), int(b[1:])
    q(
        f"m{n}",
        f"How does the {SIG[ia]} {FACT[ia]} result relate to the {SIG[ib]} {FACT[ib]} result?",
        "multihop",
        [a, b],
        [
            {"doc": a, "quote": f"{FACT[ia]} anomaly", "grade": 3},
            {"doc": b, "quote": f"{FACT[ib]} anomaly", "grade": 3},
        ],
        as_pat=n in (0, 1),
    )
for n, t in enumerate(THEME):
    keys = [f"d{i:02d}" for i in range(32) if THEME[i % 8] == t]
    q(
        f"g{n}",
        f"Give an overview of {t} across all the studies.",
        "global",
        keys,
        [{"doc": k, "quote": f"{t} protocols", "grade": 2} for k in keys],
        as_pat=n == 4,
    )
queries.sort(key=lambda x: {"fact": 0, "multihop": 1, "global": 2}[x["kind"]])
for x in queries:
    if not x["as_pat"]:
        del x["as_pat"]
out = {
    "name": "evalci",
    "principals": {k: ({"groups": v} if k != "root" else {"admin": True}) for k, v in P.items()},
    "labels": [{"name": "Secret", "exclude_llm": True}, {"name": "Internal", "block_agents": True}],
    "documents": docs,
    "queries": queries,
}
for dd in out["documents"]:
    pass
hdr = "# Synthetic eval fixture for `roracle eval ci` (generated by gen_fixture.py; hand-edit freely).\n# 32 docs, 4 principals, every visibility, one exclude_llm (d05) and one block_agents (d04) label, 40 queries.\n"
Path(sys.argv[1]).write_text(
    hdr + yaml.safe_dump(out, sort_keys=False, width=200, default_flow_style=None, allow_unicode=True)
)
print(len(docs), len(queries), sum(1 for x in queries if x.get("as_pat")))
