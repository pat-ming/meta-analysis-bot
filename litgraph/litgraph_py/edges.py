"""candidates + edges: block finding pairs, write judge batches, and merge the
judge's answers (plus code-made caution edges) into edges.jsonl."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from itertools import combinations

from . import PIPELINE_VERSION, pdftext, resolve, schema
from .io import content_hash, read_json, read_jsonl, write_json, write_jsonl
from .verify import verify

# type -> (polarity, directed)
EDGE_TYPES = {
    "replicates": ("supports", False),
    "consistent_with": ("supports", False),
    "contradicts": ("conflicts", False),
    "discordant_levels": ("conflicts", False),
    "shares_cohort": ("caution", False),
    "same_group": ("caution", False),
    "method_confound": ("caution", True),   # points from the explanation to the discrepancy
    "poolable": ("neutral", False),
    "related_entity": ("neutral", False),
}


def edge_key(source: str, target: str, etype: str) -> str:
    directed = EDGE_TYPES.get(etype, ("", False))[1]
    a, b = (source, target) if directed else tuple(sorted((source, target)))
    return f"{a}|{b}|{etype}"


def edge_id(k: str) -> str:
    return "e-" + hashlib.sha1(k.encode()).hexdigest()[:10]


def pair_key(a: str, b: str) -> str:
    return "|".join(sorted((a, b)))


def pair_id(pk: str) -> str:
    return "p-" + hashlib.sha1(pk.encode()).hexdigest()[:10]


def finding_hash(f: dict) -> str:
    """Changes when anything the judge looked at changes."""
    keep = ("claim", "finding_type", "entity", "entity_id", "comparison", "direction", "significant",
            "stats", "methods", "population", "adjusted")
    return content_hash({k: f.get(k) for k in keep} | {"quotes": [e.get("quote") for e in f.get("evidence", [])]})


def card(f: dict, paper: dict) -> dict:
    """What the judge sees of a finding."""
    c = {k: f.get(k) for k in ("finding_id", "claim", "finding_type", "entity", "entity_id", "entity_type",
                               "comparison", "direction", "significant", "stats", "methods", "population",
                               "adjusted", "covariates", "flags")}
    c["evidence"] = [{"quote": e["quote"], "page": e.get("page"), "location": e.get("location")}
                     for e in f.get("evidence", [])]
    authors = paper.get("authors") or []
    c["paper"] = {"paper_id": paper["paper_id"], "first_author": authors[0] if authors else None,
                  "last_author": authors[-1] if authors else None, "year": paper.get("year"),
                  "author_group": paper.get("author_group"), "cohort_ids": paper.get("cohort_ids", []),
                  "study_design": paper.get("study_design")}
    return c


# ------------------------------------------------------------------------ blocking

def _first_finding(findings_by_paper, pid):
    fs = findings_by_paper.get(pid) or []
    return min(fs, key=lambda f: int(f["finding_id"].rsplit("-f", 1)[1])) if fs else None


def _baselines_match(a: list[dict], b: list[dict]) -> bool:
    def rows(t):
        return {(r.get("n"), r.get("age_mean"), r.get("pct_female")) for r in t
                if r.get("n") and (r.get("age_mean") is not None or r.get("pct_female") is not None)}
    return len(rows(a) & rows(b)) >= 2


def block(project, include_comparison: bool = False):
    findings = read_jsonl(project.findings)
    corpus = {p["paper_id"]: p for p in read_json(project.corpus, [])}
    entities, groups, cohorts = resolve.load(project)
    anc = resolve.ancestors(groups)
    related = defaultdict(dict)
    for e in entities:
        for r in e.get("related") or []:
            related[e["entity_id"]][r["entity_id"]] = r.get("relation", "related")
            related[r["entity_id"]].setdefault(e["entity_id"], r.get("relation", "related"))

    by_id = {f["finding_id"]: f for f in findings}
    by_entity, by_paper = defaultdict(list), defaultdict(list)
    for f in findings:
        by_entity[f.get("entity_id") or "~" + resolve.key(f["entity"])].append(f)
        by_paper[f["paper_id"]].append(f)

    reasons = defaultdict(set)

    def add(a, b, why):
        if a["paper_id"] != b["paper_id"]:
            reasons[pair_key(a["finding_id"], b["finding_id"])].add(why)

    for fs in by_entity.values():
        for a, b in combinations(fs, 2):
            add(a, b, "same_entity")
    for e1, rel in related.items():
        for e2, relation in rel.items():
            if e1 < e2:
                for a in by_entity.get(e1, []):
                    for b in by_entity.get(e2, []):
                        add(a, b, f"related_entity:{relation}")

    def comp_ids(f):
        c = f.get("comparison", {})
        return c.get("case_id"), c.get("control_id")

    def comp_compatible(a, b):
        (ca, ka), (cb, kb) = comp_ids(a), comp_ids(b)
        if not (ca and cb):
            return False
        def overlap(x, y):
            return x == y or x in anc.get(y, []) or y in anc.get(x, [])
        return overlap(ca, cb) and (ka == kb or (ka and kb and overlap(ka, kb)))

    if include_comparison:
        by_comp = defaultdict(list)
        for f in findings:
            if all(comp_ids(f)):
                by_comp[comp_ids(f)].append(f)
        for fs in by_comp.values():
            for a, b in combinations(fs, 2):
                if a.get("entity_type") == b.get("entity_type"):
                    add(a, b, "same_comparison")
    for pk in list(reasons):
        a, b = (by_id[x] for x in pk.split("|"))
        if comp_compatible(a, b):
            reasons[pk].add("compatible_comparison")

    # Independence: shared cohorts, shared author groups, identical baselines.
    baselines = read_json(project.work / "baselines.json", {})
    cohort_name = {c["cohort_id"]: c.get("name", c["cohort_id"]) for c in cohorts}
    paper_pairs = {}
    for pa, pb in combinations(sorted(corpus), 2):
        A, B = corpus[pa], corpus[pb]
        shared = sorted(set(A.get("cohort_ids", [])) & set(B.get("cohort_ids", [])))
        why = {}
        if shared:
            why["shares_cohort"] = shared
        if A.get("author_group") and A.get("author_group") == B.get("author_group"):
            why["same_group"] = A["author_group"]
        if not shared and pa in baselines and pb in baselines and _baselines_match(baselines[pa], baselines[pb]):
            why["identical_baseline"] = True
        if why:
            paper_pairs[(pa, pb)] = why

    code_edges = []
    for (pa, pb), why in paper_pairs.items():
        linked = [pk for pk in reasons if {by_id[x]["paper_id"] for x in pk.split("|")} == {pa, pb}]
        if not linked:
            fa, fb = _first_finding(by_paper, pa), _first_finding(by_paper, pb)
            if not (fa and fb):
                continue
            linked = [pair_key(fa["finding_id"], fb["finding_id"])]
            paper_level = True
        else:
            paper_level = False
        for pk in linked:
            if "identical_baseline" in why:
                reasons[pk].add("identical_baseline")   # the judge decides whether it's one cohort
            a, b = (by_id[x] for x in pk.split("|"))
            if "shares_cohort" in why:
                code_edges.append(_cohort_edge(a, b, why["shares_cohort"], cohort_name, cohorts, paper_level))
            if "same_group" in why:
                code_edges.append(_group_edge(a, b, corpus, why["same_group"], paper_level))

    judge_pairs = {pk: r for pk, r in reasons.items()
                   if r & {"same_entity", "same_comparison", "identical_baseline"} or
                   any(x.startswith("related_entity") for x in r)}
    return findings, corpus, by_id, judge_pairs, code_edges


def _quote_for(f, cohorts=None, cohort_ids=()):
    for c in cohorts or []:
        if c["cohort_id"] in cohort_ids:
            for ev in c.get("evidence") or []:
                if ev.get("paper_id") == f["paper_id"]:
                    return {"finding_id": f["finding_id"], "quote": ev["quote"], "page": ev.get("page"),
                            "verified": ev.get("verified")}
    ev = f["evidence"][0]
    return {"finding_id": f["finding_id"], "quote": ev["quote"], "page": ev.get("page"),
            "verified": ev.get("verified")}


def _cohort_edge(a, b, shared, cohort_name, cohorts, paper_level):
    names = ", ".join(cohort_name.get(c, c) for c in shared)
    q = ["paper-level: these papers share no measured entity"] if paper_level else []
    return {"source": a["finding_id"], "target": b["finding_id"], "type": "shares_cohort",
            "rationale": f"Both papers draw on the same cohort ({names}); these findings are not independent.",
            "qualifiers": q, "evidence": [_quote_for(a, cohorts, shared), _quote_for(b, cohorts, shared)],
            "confidence": 0.95, "created_by": "code"}


def _group_edge(a, b, corpus, gid, paper_level):
    ka = {resolve_author(x) for x in corpus[a["paper_id"]].get("authors") or []}
    kb = {resolve_author(x) for x in corpus[b["paper_id"]].get("authors") or []}
    common = sorted(ka & kb)
    q = ["paper-level: these papers share no measured entity"] if paper_level else []
    who = ", ".join(n.title() for n in common[:4]) or "a shared last author"
    return {"source": a["finding_id"], "target": b["finding_id"], "type": "same_group",
            "rationale": f"Same research group ({gid}; shared authors: {who}); agreement here is not independent replication.",
            "qualifiers": q, "evidence": [_quote_for(a), _quote_for(b)],
            "confidence": 0.9, "created_by": "code"}


def resolve_author(name):
    from .validate import author_key
    return author_key(name)


# ----------------------------------------------------------------------- candidates

def candidates(project, batch_size: int = 20, include_comparison: bool = False) -> dict:
    project.ensure()
    absorbed = merge_judged_into_cache(project)  # never lose answers from a previous round
    findings, corpus, by_id, judge_pairs, code_edges = block(project, include_comparison)
    cache = read_json(project.judge_cache, {})
    hashes = {f["finding_id"]: finding_hash(f) for f in findings}

    todo = []
    for pk, why in sorted(judge_pairs.items()):
        a, b = pk.split("|")
        hit = cache.get(pk)
        if hit and hit.get("hashes") == {a: hashes[a], b: hashes[b]}:
            continue
        todo.append((pk, why))

    for p in project.candidates.glob("batch-*.json"):
        p.unlink()
    for p in project.judged.glob("batch-*.json"):
        p.unlink()

    # Keep pairs about the same entity together so the judge sees a coherent batch.
    todo.sort(key=lambda t: (min(by_id[x].get("entity_id") or by_id[x]["entity"] for x in t[0].split("|")), t[0]))
    batches = []
    for i in range(0, len(todo), batch_size):
        bid = f"batch-{i // batch_size + 1:03d}"
        pairs = []
        for pk, why in todo[i:i + batch_size]:
            a, b = (by_id[x] for x in pk.split("|"))
            pairs.append({"pair_id": pair_id(pk), "reasons": sorted(why),
                          "a": card(a, corpus[a["paper_id"]]), "b": card(b, corpus[b["paper_id"]]),
                          "hashes": {a["finding_id"]: hashes[a["finding_id"]], b["finding_id"]: hashes[b["finding_id"]]}})
        out_path = project.judged / f"{bid}.json"
        write_json(project.candidates / f"{bid}.json",
                   {"batch_id": bid, "out_path": str(out_path), "pairs": pairs})
        batches.append({"batch_id": bid, "path": str(project.candidates / f"{bid}.json"),
                        "out_path": str(out_path), "pairs": len(pairs)})
    write_json(project.candidates / "index.json", {"project": str(project.root), "batches": batches})
    write_json(project.work / "code_edges.json", code_edges)
    return {"candidate_pairs": len(judge_pairs), "cached": len(judge_pairs) - len(todo), "to_judge": len(todo),
            "batches": len(batches), "code_edges": len(code_edges), "absorbed_previous": absorbed,
            "index_path": str(project.candidates / "index.json")}


# ---------------------------------------------------------------------------- merge

def check_judgments(project, judged_path, batch=None) -> list[str]:
    data = read_json(judged_path)
    if data is None:
        return [f"{judged_path}: file not found"]
    errs = schema.errors(data, "judgments.schema.json")
    if errs:
        return errs
    if batch is None:
        batch = read_json(project.candidates / f"{data['batch_id']}.json")
        if batch is None:
            return [f"no candidate batch {data['batch_id']}"]
    pairs = {p["pair_id"]: p for p in batch["pairs"]}
    seen = set()
    for i, j in enumerate(data["judgments"]):
        p = pairs.get(j["pair_id"])
        if not p:
            errs.append(f"$.judgments[{i}]: unknown pair_id {j['pair_id']}")
            continue
        seen.add(j["pair_id"])
        ends = {p["a"]["finding_id"], p["b"]["finding_id"]}
        for k, e in enumerate(j["edges"]):
            where = f"$.judgments[{i}].edges[{k}]"
            if {e["source"], e["target"]} != ends:
                errs.append(f"{where}: source/target must be this pair's findings {sorted(ends)}")
            cited = {ev["finding_id"] for ev in e["evidence"]}
            if not ends <= cited:
                errs.append(f"{where}: evidence must quote both findings (missing {sorted(ends - cited)})")
    missing = set(pairs) - seen
    if missing:
        errs.append(f"{len(missing)} pair(s) have no judgment (give `edges: []` with a no_edge_reason): "
                    + ", ".join(sorted(missing)[:10]))
    return errs


def merge_judged_into_cache(project) -> int:
    cache = read_json(project.judge_cache, {})
    n = 0
    for out in sorted(project.judged.glob("batch-*.json")):
        batch = read_json(project.candidates / out.name)
        data = read_json(out)
        if not batch or not data:
            continue
        pairs = {p["pair_id"]: p for p in batch["pairs"]}
        for j in data.get("judgments", []):
            p = pairs.get(j.get("pair_id"))
            if not p:
                continue
            ends = {p["a"]["finding_id"], p["b"]["finding_id"]}
            good = [e for e in j.get("edges", []) if isinstance(e, dict) and {e.get("source"), e.get("target")} == ends
                    and e.get("type") in EDGE_TYPES]
            cache[pair_key(*ends)] = {"hashes": p["hashes"], "edges": good,
                                      "no_edge_reason": j.get("no_edge_reason"), "batch": data.get("batch_id")}
            n += 1
    write_json(project.judge_cache, cache)
    return n


def _normalize(e: dict, by_id: dict, corpus: dict, pages_for) -> dict | None:
    etype = e["type"]
    polarity, directed = EDGE_TYPES[etype]
    s, t = e["source"], e["target"]
    if s not in by_id or t not in by_id:
        return None
    if not directed:
        s, t = sorted((s, t))
    k = edge_key(s, t, etype)
    flags = list(e.get("flags") or [])
    evidence = []
    for ev in e.get("evidence", []):
        f = by_id.get(ev.get("finding_id"))
        if not f:
            continue
        r = verify(ev.get("quote", ""), pages_for(f["paper_id"]), ev.get("page"))
        evidence.append({"finding_id": f["finding_id"], "quote": ev.get("quote", ""), "page": ev.get("page"),
                         "verified": r["status"], "found_page": r["found_page"]})
        if r["status"] in ("not_found", "wrong_page"):
            flags.append("quote_" + r["status"])
    for end in (s, t):
        if end not in {ev["finding_id"] for ev in evidence}:
            flags.append("missing_evidence")
    conf = e.get("confidence", 0.5)
    conf = min(1.0, max(0.0, float(conf))) if isinstance(conf, (int, float)) else 0.5
    return {"edge_id": edge_id(k), "edge_key": k, "source": s, "target": t, "type": etype, "polarity": polarity,
            "directed": directed, "rationale": e.get("rationale", ""), "qualifiers": e.get("qualifiers") or [],
            "evidence": evidence, "confidence": round(conf, 3), "created_by": e.get("created_by", "edge-judge"),
            "pipeline_version": PIPELINE_VERSION, "flags": sorted(set(flags))}


def merge(project) -> dict:
    absorbed = merge_judged_into_cache(project)
    findings = read_jsonl(project.findings)
    by_id = {f["finding_id"]: f for f in findings}
    corpus = {p["paper_id"]: p for p in read_json(project.corpus, [])}
    hashes = {fid: finding_hash(f) for fid, f in by_id.items()}
    pages_cache = {}

    def pages_for(pid):
        if pid not in pages_cache:
            pages_cache[pid] = pdftext.cached_pages(project, corpus[pid]) if pid in corpus else None
        return pages_cache[pid]

    raw = list(read_json(project.work / "code_edges.json", []))
    stale = 0
    for pk, hit in read_json(project.judge_cache, {}).items():
        a, b = pk.split("|")
        if a not in by_id or b not in by_id or hit.get("hashes") != {a: hashes[a], b: hashes[b]}:
            stale += 1
            continue
        raw.extend(hit["edges"])

    out = {}
    for e in raw:
        n = _normalize(e, by_id, corpus, pages_for)
        if n and (n["edge_key"] not in out or n["confidence"] > out[n["edge_key"]]["confidence"]):
            out[n["edge_key"]] = n
    edges = sorted(out.values(), key=lambda e: (e["polarity"], e["type"], e["edge_key"]))
    write_jsonl(project.edges, edges)

    pending = []
    for idx in sorted(project.candidates.glob("batch-*.json")):
        batch = read_json(idx)
        if not (project.judged / idx.name).exists():
            pending.append(batch["batch_id"])
    by_type = defaultdict(int)
    for e in edges:
        by_type[e["type"]] += 1
    return {"edges": len(edges), "by_type": dict(sorted(by_type.items())), "absorbed_judgments": absorbed,
            "stale_cache_entries": stale, "batches_without_output": pending,
            "flagged": sum(1 for e in edges if e["flags"])}
