"""build: render a self-contained graph.html (data and vendored JS inlined)."""

from __future__ import annotations

import datetime as dt
import json

from . import PIPELINE_VERSION
from .io import VIEWER_DIR, read_json, read_jsonl
from .report import cite
from .reviews import load_state

DATA_SLOT = "__LITGRAPH_DATA__"
SCRIPT_SLOT = "/*__LITGRAPH_VENDOR__*/"


def graph_data(project, live: bool = False) -> dict:
    findings = read_jsonl(project.findings)
    corpus = read_json(project.corpus, [])
    ents = read_json(project.entities, {}) or {}
    cohorts = (read_json(project.cohorts, {}) or {}).get("cohorts", [])
    cfg = project.load_config()
    state = load_state(project, findings)

    papers = []
    for p in corpus:
        papers.append({k: p.get(k) for k in ("paper_id", "title", "authors", "year", "journal", "doi", "pmid",
                                              "author_group", "cohort_ids", "study_design")}
                      | {"cite": cite(p), "has_pdf": (project.root / p["pdf_path"]).exists()})
    for f in findings:
        ns = [g.get("n") for g in (f.get("stats") or {}).get("groups", []) if isinstance(g.get("n"), int)]
        f["n"] = sum(ns) if ns else (f.get("population") or {}).get("n_total")

    edges = []
    for e in read_jsonl(project.edges):
        s = state.get(e["edge_key"], {})
        edges.append(dict(e, review={"status": s.get("status", "unreviewed"), "type": s.get("type"),
                                     "notes": s.get("notes", []), "reviewer": s.get("reviewer"),
                                     "timestamp": s.get("timestamp")}))
    built = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    return {
        "meta": {"topic": cfg.get("topic") or project.root.name, "built_at": built,
                 "pipeline_version": PIPELINE_VERSION, "live": live,
                 "counts": {"papers": len(papers), "findings": len(findings), "edges": len(edges)}},
        "papers": papers, "findings": findings, "edges": edges,
        "entities": ents.get("entities", []), "groups": ents.get("groups", []), "cohorts": cohorts,
        "positions": read_json(project.positions, {}) or {},
    }


def _vendor() -> str:
    parts = []
    for name in ("cytoscape.min.js", "cytoscape-svg.js"):
        p = VIEWER_DIR / "vendor" / name
        if p.exists():
            parts.append(p.read_text(encoding="utf-8").replace("</script", "<\\/script"))
    return "\n;\n".join(parts)


def render(project, live: bool = False) -> str:
    template = (VIEWER_DIR / "template.html").read_text(encoding="utf-8")
    data = json.dumps(graph_data(project, live), ensure_ascii=False)
    data = data.replace("</", "<\\/").replace("<!--", "<\\!--")
    return template.replace(SCRIPT_SLOT, _vendor(), 1).replace(DATA_SLOT, data, 1)


def run(project) -> dict:
    if not project.findings.exists():
        raise SystemExit("litgraph build: no findings.jsonl; run the synthesize pipeline first")
    html = render(project, live=False)
    project.graph.write_text(html, encoding="utf-8")
    return {"graph": str(project.graph), "bytes": len(html.encode("utf-8"))}
