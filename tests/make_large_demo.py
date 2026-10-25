"""Build graphs/large-demo: a synthetic 30-paper review (about 300 findings) for
checking how the viewer copes with a realistic size. No PDFs; data is random.

    python3 tests/make_large_demo.py && python3 start.py
"""

import random
import sys
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "litgraph"))

from litgraph_py.edges import EDGE_TYPES, edge_id, edge_key  # noqa: E402
from litgraph_py.io import Project, write_json, write_jsonl  # noqa: E402

rng = random.Random(7)
project = Project(ROOT / "graphs" / "large-demo").ensure()

SURNAMES = ["Chen", "Lattekivi", "Pasquali", "Ikeme", "Rossi", "Okafor", "Nakamura", "Silva", "Novak", "Haddad",
            "Kowalski", "Jensen", "Moreau", "Ivanova", "Dubois", "Tanaka", "Kumar", "Garcia", "Larsen", "Fischer"]
ENTITIES = ([f"miR-{n}{arm}" for n in (21, 26, 92, 124, 146, 155, 192, 199, 206, 223, 338, 451) for arm in ("-3p", "-5p")]
            + ["TDP-43", "NfL", "SOD1", "FUS", "alpha-synuclein", "tau", "GFAP", "CD63", "CD81", "L1CAM", "RANKL", "UMOD"])
TYPES = ["group_difference"] * 6 + ["diagnostic_accuracy", "correlation", "longitudinal_change", "null_result"]
FLUIDS = ["plasma", "serum", "CSF"]

papers, findings = [], []
for i in range(30):
    sur = rng.choice(SURNAMES)
    year = rng.randint(2012, 2025)
    pid = f"{sur.lower()}{year}{'abcdefghij'[i % 10]}"
    last = rng.choice(SURNAMES[:6])
    papers.append({"paper_id": pid, "title": f"Extracellular vesicle biomarkers in ALS: study {i + 1}",
                   "authors": [f"{sur} A", f"{rng.choice(SURNAMES)} B", f"{last} Z"], "year": year,
                   "journal": "Synthetic J", "doi": None, "pmid": None, "pdf_path": f"papers/{pid}.pdf",
                   "file_hash": f"sha256:{i}", "author_group": f"group-{last.lower()}",
                   "cohort_ids": ["cohort-a"] if i % 7 == 0 else []})
    fluid = rng.choice(FLUIDS)
    ents = rng.sample(ENTITIES[:12], 4) + rng.sample(ENTITIES[12:], 4) + rng.sample(ENTITIES, 3)
    for n, ent in enumerate(dict.fromkeys(ents), 1):
        ft = rng.choice(TYPES)
        d = "none" if ft == "null_result" else rng.choice(["up", "up", "down"])
        size = rng.choice([None, 20, 40, 80, 150, 400])
        findings.append({
            "finding_id": f"{pid}-f{n}", "paper_id": pid, "claim": f"{ent} is {d} in ALS vs controls ({fluid} EVs).",
            "finding_type": ft, "entity": ent, "entity_id": ent.lower(), "entity_type": "miRNA" if ent.startswith("miR") else "protein",
            "comparison": {"case": "ALS", "control": "controls", "case_id": "als", "control_id": "hc"},
            "direction": d, "significant": d != "none",
            "stats": {"groups": [{"label": "ALS", "n": size, "value": None, "spread": None, "stat_type": "mean_sd"}]},
            "methods": {"biofluid": fluid, "ev_type": "neuronal", "assay": "qPCR"}, "population": {},
            "evidence": [{"quote": f"Synthetic quote about {ent}.", "page": 3, "verified": "verified"}],
            "verified_quote": rng.random() > 0.05, "flags": ["implausible_spread"] if rng.random() < 0.05 else []})

edges = []
by_ent = {}
for f in findings:
    by_ent.setdefault(f["entity_id"], []).append(f)
for fs in by_ent.values():
    for a, b in combinations(fs, 2):
        if a["paper_id"] == b["paper_id"] or rng.random() > 0.35:
            continue
        if a["direction"] == b["direction"] != "none":
            types = ["replicates"] + (["poolable"] if rng.random() < 0.3 else [])
        else:
            types = ["contradicts"] + (["method_confound"] if rng.random() < 0.4 else [])
        for t in types:
            k = edge_key(a["finding_id"], b["finding_id"], t)
            s, tg = k.split("|")[:2]
            edges.append({"edge_id": edge_id(k), "edge_key": k, "source": s, "target": tg, "type": t,
                          "polarity": EDGE_TYPES[t][0], "directed": EDGE_TYPES[t][1], "rationale": "Synthetic connection.",
                          "qualifiers": [], "evidence": [], "confidence": round(rng.uniform(0.45, 0.95), 2),
                          "created_by": "demo", "flags": []})
for a, b in combinations(papers, 2):
    if a["author_group"] == b["author_group"] and rng.random() < 0.25:
        fa, fb = f"{a['paper_id']}-f1", f"{b['paper_id']}-f1"
        k = edge_key(fa, fb, "same_group")
        s, tg = k.split("|")[:2]
        edges.append({"edge_id": edge_id(k), "edge_key": k, "source": s, "target": tg, "type": "same_group",
                      "polarity": "caution", "directed": False, "rationale": "Synthetic.", "qualifiers": [],
                      "evidence": [], "confidence": 0.9, "created_by": "demo", "flags": []})

write_json(project.config, {"topic": "Synthetic ALS EV review (30 papers)"})
write_json(project.corpus, papers)
write_jsonl(project.findings, findings)
write_jsonl(project.edges, edges)
write_json(project.entities, {"entities": [{"entity_id": e.lower(), "canonical": e, "type": "x", "aliases": []} for e in ENTITIES],
                              "groups": []})
(project.reports / "connections.md").write_text("# Synthetic\n")
for p in (project.positions, project.reviews):
    p.unlink(missing_ok=True)
print(f"{len(papers)} papers, {len(findings)} findings, {len(edges)} edges -> {project.root}")
