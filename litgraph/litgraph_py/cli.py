"""litgraph command line. Every command prints a JSON summary on stdout so the
synthesize skill can read results without parsing prose."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, build, edges, ingest, pdftext, report, resolve, validate
from .io import Project, read_json, read_jsonl, write_json
from .verify import verify


def _out(obj, code=0):
    print(json.dumps(obj, indent=2, ensure_ascii=False))
    return code


def cmd_init(a, p):
    p.ensure()
    cfg = p.load_config()
    if a.topic:
        cfg["topic"] = a.topic
    cfg.setdefault("topic", p.root.name)
    cfg["pipeline_version"] = __version__
    write_json(p.config, cfg)
    return _out({"project": str(p.root), "config": cfg, "text_backend": pdftext.backend()})


def cmd_ingest(a, p):
    return _out(ingest.run(p, a.papers))


def cmd_check_extraction(a, p):
    """Self-check for the extractor: schema + quote verification on one file."""
    ext = read_json(Path(a.file))
    if ext is None:
        return _out({"ok": False, "errors": [f"{a.file}: not found"]}, 1)
    errs = validate.check_extraction(ext)
    quotes = []
    corpus = {x["paper_id"]: x for x in read_json(p.corpus, [])}
    entry = corpus.get(ext.get("paper_id"))
    if entry and not errs:
        if ext.get("file_hash") != entry["file_hash"]:
            errs.append(f"file_hash does not match corpus.json ({entry['file_hash']})")
        pages = pdftext.cached_pages(p, entry)
        for f in ext.get("findings", []):
            for ev in f.get("evidence", []):
                r = verify(ev.get("quote", ""), pages, ev.get("page"))
                if r["status"] not in ("verified", "unchecked"):
                    quotes.append({"finding_id": f.get("finding_id"), "page": ev.get("page"),
                                   "status": r["status"], "found_page": r["found_page"],
                                   "quote": ev.get("quote", "")[:120]})
            flags = validate.plausibility(f)
            if flags:
                quotes.append({"finding_id": f.get("finding_id"), "plausibility_flags": flags})
    elif not entry:
        errs.append(f"paper_id {ext.get('paper_id')!r} is not in corpus.json")
    return _out({"ok": not errs, "errors": errs, "quote_and_plausibility_issues": quotes,
                 "findings": len(ext.get("findings", []))}, 1 if errs else 0)


def cmd_verify_quote(a, p):
    corpus = {x["paper_id"]: x for x in read_json(p.corpus, [])}
    if a.paper not in corpus:
        return _out({"error": f"unknown paper {a.paper}"}, 1)
    return _out(verify(a.quote, pdftext.cached_pages(p, corpus[a.paper]), a.page))


def cmd_validate(a, p):
    return _out(validate.run(p))


def cmd_resolve(a, p):
    if a.action == "prepare":
        return _out(resolve.prepare(p))
    r = resolve.apply(p)
    return _out(r, 1 if r["problems"] else 0)


def cmd_candidates(a, p):
    return _out(edges.candidates(p, a.batch_size, a.include_comparison))


def cmd_check_judgments(a, p):
    errs = edges.check_judgments(p, Path(a.file))
    return _out({"ok": not errs, "errors": errs[:50]}, 1 if errs else 0)


def cmd_edges(a, p):
    return _out(edges.merge(p))


def cmd_report(a, p):
    if a.narrative:
        text = Path(a.narrative).read_text(encoding="utf-8") if a.narrative != "-" else sys.stdin.read()
        report.set_narrative(p, text)
        return _out({"ok": True, "connections_md": str(p.reports / "connections.md")})
    return _out(report.run(p))


def cmd_build(a, p):
    r = build.run(p)
    if a.open:
        import webbrowser
        webbrowser.open(p.graph.as_uri())
    return _out(r)


def cmd_view(a, p):
    from . import server
    if not a.no_build:
        build.run(p)
    server.serve(p, a.port, not a.no_open, a.reviewer)
    return 0


def cmd_status(a, p):
    corpus = read_json(p.corpus, [])
    findings = read_jsonl(p.findings)
    exts = {x.stem: read_json(x) for x in p.extractions.glob("*.json")} if p.extractions.exists() else {}
    extracted = sum(1 for c in corpus if (exts.get(c["paper_id"]) or {}).get("file_hash") == c["file_hash"])
    idx = read_json(p.candidates / "index.json", {"batches": []})
    judged = sum(1 for b in idx["batches"] if Path(b["out_path"]).exists())
    return _out({
        "project": str(p.root), "initialized": p.config.exists(), "text_backend": pdftext.backend(),
        "papers": len(corpus), "extracted": extracted, "findings": len(findings),
        "entities_resolved": sum(1 for f in findings if f.get("entity_id")),
        "has_entities_json": p.entities.exists(), "has_cohorts_json": p.cohorts.exists(),
        "candidate_batches": len(idx["batches"]), "judged_batches": judged,
        "edges": len(read_jsonl(p.edges)), "reviews": len(read_jsonl(p.reviews)),
        "report": (p.reports / "connections.md").exists(), "graph": p.graph.exists(),
    })


def main(argv=None):
    ap = argparse.ArgumentParser(prog="litgraph", description="litgraph: findings graph for systematic reviews")
    ap.add_argument("--version", action="version", version=f"litgraph {__version__}")
    ap.add_argument("-C", "--project", default=".", help="review project folder (default: current directory)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create litgraph/ in the project")
    s.add_argument("--topic")
    s.set_defaults(fn=cmd_init)
    s = sub.add_parser("ingest", help="hash PDFs, detect DOI/PMID, list papers needing extraction")
    s.add_argument("papers", nargs="?", default="papers")
    s.set_defaults(fn=cmd_ingest)
    s = sub.add_parser("check-extraction", help="check one extraction file (schema, quotes, plausibility)")
    s.add_argument("file")
    s.set_defaults(fn=cmd_check_extraction)
    s = sub.add_parser("verify-quote", help="check whether a quote is on a page")
    s.add_argument("--paper", required=True)
    s.add_argument("--page", type=int)
    s.add_argument("quote")
    s.set_defaults(fn=cmd_verify_quote)
    s = sub.add_parser("validate", help="validate extractions and write findings.jsonl")
    s.set_defaults(fn=cmd_validate)
    s = sub.add_parser("resolve", help="prepare input for, or apply, entity resolution")
    s.add_argument("action", choices=["prepare", "apply"])
    s.set_defaults(fn=cmd_resolve)
    s = sub.add_parser("candidates", help="block finding pairs into judge batches")
    s.add_argument("--batch-size", type=int, default=20)
    s.add_argument("--include-comparison", action="store_true",
                   help="also pair findings that share only a comparison (more pairs)")
    s.set_defaults(fn=cmd_candidates)
    s = sub.add_parser("check-judgments", help="check one edge-judge output file")
    s.add_argument("file")
    s.set_defaults(fn=cmd_check_judgments)
    s = sub.add_parser("edges", help="merge judged and code edges into edges.jsonl")
    s.add_argument("action", choices=["merge"])
    s.set_defaults(fn=cmd_edges)
    s = sub.add_parser("report", help="write reports/connections.md and poolable CSVs")
    s.add_argument("--narrative", help="file (or - for stdin) with the narrative summary to insert")
    s.set_defaults(fn=cmd_report)
    s = sub.add_parser("build", help="write a self-contained graph.html")
    s.add_argument("--open", action="store_true")
    s.set_defaults(fn=cmd_build)
    s = sub.add_parser("view", help="build, then serve the viewer on localhost")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-open", action="store_true")
    s.add_argument("--no-build", action="store_true")
    s.add_argument("--reviewer", help="name recorded on review decisions (default: your login)")
    s.set_defaults(fn=cmd_view)
    s = sub.add_parser("status", help="where the pipeline is")
    s.set_defaults(fn=cmd_status)

    a = ap.parse_args(argv)
    return a.fn(a, Project(a.project))


if __name__ == "__main__":
    sys.exit(main())
