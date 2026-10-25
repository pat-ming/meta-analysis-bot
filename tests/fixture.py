"""Builds a small fake review project: real PDFs plus hand-written stand-ins for
what the paper-extractor and edge-judge subagents would produce."""

from __future__ import annotations

import json
from pathlib import Path


def make_pdf(path: Path, pages: list[list[str]]):
    """Minimal multi-page text PDF (Helvetica, ASCII) that pdftotext/pymupdf can read."""
    objs = []

    def add(body: bytes) -> int:
        objs.append(body)
        return len(objs)

    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    pages_id = len(objs) + 1 + 2 * len(pages)  # reserved after page/content pairs
    kids = []
    for lines in pages:
        esc = [ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for ln in lines]
        stream = "BT /F1 10 Tf 14 TL 50 760 Td " + " ".join(f"({ln}) Tj T*" for ln in esc) + " ET"
        data = stream.encode("latin-1")
        content = add(b"<< /Length %d >>\nstream\n" % len(data) + data + b"\nendstream")
        page = add(b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 792] /Contents %d 0 R "
                   b"/Resources << /Font << /F1 %d 0 R >> >> >>" % (pages_id, content, font))
        kids.append(page)
    assert add(b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
        b" ".join(b"%d 0 R" % k for k in kids), len(kids))) == pages_id
    catalog = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id)

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, catalog, xref)
    path.write_bytes(bytes(out))


# ---------------------------------------------------------------- paper texts

PAPERS = {
    "lattekivi2022.pdf": [
        ["Profiling serum extracellular vesicles in psoriasis and psoriatic arthritis",
         "Lattekivi F, Kingo K, Salumets A, Koks S",
         "doi: 10.3390/ijms23010001",
         "Patients were recruited from the PsoBank cohort at Tartu University Hospital."],
        ["Results",
         "EV miR-26a-5p was significantly higher in PsA than in PsV patients (p = 0.004).",
         "Serum EV miR-192-5p was elevated in PsA compared with PsV (p = 0.01).",
         "Table 2. miR-26a-5p PsA 4.1 (1.2) n=25; PsV 2.9 (1.0) n=25"],
    ],
    "pasquali2020.pdf": [
        ["Plasma extracellular vesicle microRNAs in psoriatic disease",
         "Pasquali L, Svedbom A, Srivastava A, Stahle M",
         "PMID: 31234567"],
        ["Results",
         "Plasma EV miR-26a-5p levels were increased in PsA relative to cutaneous psoriasis (p < 0.001).",
         "In contrast, miR-192-5p was decreased in PsA versus PsC (p = 0.02).",
         "Table 3. miR-26a-5p PsA 3.8 +/- 0.3 (SEM) n=30; PsC 2.7 +/- 0.25 (SEM) n=28"],
    ],
    "10.1234_abc.2023.pdf": [  # provisional id: the extractor suggests chen2023
        ["Neuronal EV cargo in psoriatic arthritis",
         "Chen Y, Wang L, Kingo K, Koks S",
         "Participants were drawn from the PsoBank cohort."],
        ["Results",
         "miR-199a-5p was up-regulated in PsA EVs compared with PsV EVs (p = 0.03).",
         "We found no difference in miR-26a-5p between PsA and PsV (p = 0.41)."],
    ],
}


def extraction(paper_id, file_hash, paper, findings, cohorts=(), baseline=None):
    return {"paper_id": paper_id, "file_hash": file_hash, "extractor_version": "test", "paper": paper,
            "cohorts": list(cohorts), "baseline": baseline, "findings": findings}


def F(pid, n, claim, entity, case, control, direction, sig, quote, page, groups=None, biofluid="serum",
      ftype="group_difference", p=None):
    return {"finding_id": f"{pid}-f{n}", "claim": claim, "finding_type": ftype, "entity": entity, "entity_type": "miRNA",
            "comparison": {"case": case, "control": control, "subgroup": None}, "direction": direction,
            "significant": sig, "stats": {"groups": groups or [], "effect": None, "p_value": p},
            "methods": {"biofluid": biofluid, "ev_type": "general", "isolation": "SEC", "assay": "qPCR"},
            "population": {"n_total": None}, "adjusted": False,
            "evidence": [{"quote": quote, "page": page, "location": "Results"}], "extraction_confidence": 0.9}


def write_extractions(project_dir: Path, todo: dict):
    by_pdf = {Path(p["pdf_path"]).name: p for p in todo["papers"]}
    out = {}

    t = by_pdf["lattekivi2022.pdf"]
    out[t["out_path"]] = extraction("lattekivi2022", t["file_hash"],
        {"title": "Profiling serum extracellular vesicles in psoriasis and psoriatic arthritis",
         "authors": ["Lattekivi F", "Kingo K", "Salumets A", "Koks S"], "year": 2022, "journal": "Int J Mol Sci",
         "doi": "10.3390/ijms23010001", "suggested_paper_id": "lattekivi2022"},
        [F("lattekivi2022", 1, "EV miR-26a-5p is higher in PsA than in PsV.", "miR-26a-5p", "PsA", "PsV", "up", True,
           "EV miR-26a-5p was significantly higher in PsA than in PsV patients", 2,
           [{"label": "PsA", "n": 25, "value": 4.1, "spread": 1.2, "stat_type": "mean_sd"},
            {"label": "PsV", "n": 25, "value": 2.9, "spread": 1.0, "stat_type": "mean_sd"}], p=0.004),
         F("lattekivi2022", 2, "Serum EV miR-192-5p is higher in PsA than in PsV.", "hsa-miR-192-5p", "PsA", "PsV", "up", True,
           "Serum EV miR-192-5p was elevated in PsA compared with PsV", 2, p=0.01)],
        cohorts=[{"name": "PsoBank", "evidence": [{"quote": "Patients were recruited from the PsoBank cohort", "page": 1}]}],
        baseline=[{"label": "PsA", "n": 25, "age_mean": 48.2, "pct_female": 52}])

    t = by_pdf["pasquali2020.pdf"]
    out[t["out_path"]] = extraction("pasquali2020", t["file_hash"],
        {"title": "Plasma extracellular vesicle microRNAs in psoriatic disease",
         "authors": ["Pasquali L", "Svedbom A", "Srivastava A", "Stahle M"], "year": 2020, "pmid": "31234567"},
        [F("pasquali2020", 1, "Plasma EV miR-26a-5p is higher in PsA than in cutaneous psoriasis.", "miRNA-26a-5p", "PsA", "PsC",
           "up", True, "Plasma EV miR-26a-5p levels were increased in PsA relative to cutaneous psoriasis", 2,
           [{"label": "PsA", "n": 30, "value": 3.8, "spread": 0.3, "stat_type": "mean_sem"},
            {"label": "PsC", "n": 28, "value": 2.7, "spread": 0.25, "stat_type": "mean_sem"}], biofluid="plasma", p="<0.001"),
         F("pasquali2020", 2, "miR-192-5p is lower in PsA than in PsC.", "miR-192-5p", "PsA", "PsC", "down", True,
           "In contrast, miR-192-5p was decreased in PsA versus PsC", 1, biofluid="plasma", p=0.02)])  # wrong page on purpose

    t = by_pdf["10.1234_abc.2023.pdf"]
    pid = t["paper_id"]
    out[t["out_path"]] = extraction(pid, t["file_hash"],
        {"title": "Neuronal EV cargo in psoriatic arthritis", "authors": ["Chen Y", "Wang L", "Kingo K", "Koks S"],
         "year": 2023, "suggested_paper_id": "chen2023"},
        [F(pid, 1, "miR-199a-5p is higher in PsA EVs than in PsV EVs.", "miR-199a-5p", "PsA", "PsV", "up", True,
           "miR-199a-5p was up-regulated in PsA EVs compared with PsV EVs", 2, p=0.03),
         F(pid, 2, "miR-26a-5p does not differ between PsA and PsV.", "miR-26a-5p", "PsA", "PsV", "none", False,
           "We found no difference in miR-26a-5p between PsA and PsV", 2, ftype="null_result", p=0.41)],
        cohorts=[{"name": "PsoBank", "evidence": [{"quote": "Participants were drawn from the PsoBank cohort.", "page": 1}]}])

    for path, data in out.items():
        Path(path).write_text(json.dumps(data, indent=2))


ENTITIES = {
    "entities": [
        {"entity_id": "mir-26a-5p", "canonical": "miR-26a-5p", "type": "miRNA", "aliases": ["miRNA-26a-5p"], "related": []},
        {"entity_id": "mir-192-5p", "canonical": "miR-192-5p", "type": "miRNA", "aliases": ["hsa-miR-192-5p"], "related": []},
        {"entity_id": "mir-199a-5p", "canonical": "miR-199a-5p", "type": "miRNA", "aliases": [],
         "related": [{"entity_id": "mir-26a-5p", "relation": "test_relation"}]},
    ],
    "groups": [
        {"group_id": "psa", "canonical": "Psoriatic arthritis", "aliases": ["PsA"], "parent": None},
        {"group_id": "non-psa", "canonical": "Psoriasis without arthritis", "aliases": ["PsC", "cutaneous psoriasis"], "parent": None},
        {"group_id": "psv", "canonical": "Plaque psoriasis", "aliases": ["PsV"], "parent": "non-psa"},
    ],
}


def cohorts(paper_ids):
    return {"cohorts": [{"cohort_id": "psobank", "name": "PsoBank", "aliases": [], "registry_id": None,
                         "papers": paper_ids,
                         "evidence": [{"paper_id": "lattekivi2022", "quote": "Patients were recruited from the PsoBank cohort", "page": 1},
                                      {"paper_id": "chen2023", "quote": "Participants were drawn from the PsoBank cohort.", "page": 1}]}]}


def fake_judge(batch: dict) -> dict:
    """Rule-based stand-in for the edge-judge subagent."""
    out = []
    for p in batch["pairs"]:
        a, b = p["a"], p["b"]
        ev = [{"finding_id": x["finding_id"], "quote": x["evidence"][0]["quote"], "page": x["evidence"][0]["page"]} for x in (a, b)]
        edges = []
        if a["entity_id"] == b["entity_id"]:
            if a["direction"] == b["direction"] and a["significant"] and b["significant"]:
                edges.append({"type": "replicates", "confidence": 0.8})
                if a["stats"]["groups"] and b["stats"]["groups"]:
                    edges.append({"type": "poolable", "confidence": 0.7})
            else:
                edges.append({"type": "contradicts", "confidence": 0.65})
                if a["methods"]["biofluid"] != b["methods"]["biofluid"]:
                    edges.append({"type": "method_confound", "confidence": 0.4})
        else:
            edges.append({"type": "related_entity", "confidence": 0.5})
        out.append({"pair_id": p["pair_id"], "edges": [dict(e, source=a["finding_id"], target=b["finding_id"],
                    rationale="Rule-based test judgment for this pair.", qualifiers=[], evidence=ev) for e in edges]})
    return {"batch_id": batch["batch_id"], "judgments": out}
