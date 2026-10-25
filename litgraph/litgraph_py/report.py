"""report: connections.md (tables by code, narrative by the model) and
metafor-ready poolable CSVs. Statistic conversions are done here, with the
method recorded on every converted row."""

from __future__ import annotations

import csv
import datetime as dt
import math
import re
from collections import defaultdict
from statistics import NormalDist

from . import PIPELINE_VERSION
from .edges import EDGE_TYPES
from .io import read_json, read_jsonl, write_json
from .reviews import load_state

NARRATIVE_START = "<!-- litgraph:narrative:start -->"
NARRATIVE_END = "<!-- litgraph:narrative:end -->"
_Z = NormalDist()


# ----------------------------------------------------------------- conversions

def t975(df: int) -> float:
    """Two-sided 95% t quantile (Cornish-Fisher expansion; good to ~1e-3 for df >= 3)."""
    z = 1.959963985
    if df <= 0:
        return z
    g1 = (z ** 3 + z) / 4
    g2 = (5 * z ** 5 + 16 * z ** 3 + 3 * z) / 96
    g3 = (3 * z ** 7 + 19 * z ** 5 + 17 * z ** 3 - 15 * z) / 384
    return z + g1 / df + g2 / df ** 2 + g3 / df ** 3


def to_mean_sd(g: dict) -> tuple[float | None, float | None, str]:
    """(mean, sd, method). method is '' when no conversion was needed."""
    st, v, sp, n = g.get("stat_type"), g.get("value"), g.get("spread"), g.get("n")
    num = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool)
    pair = isinstance(sp, list) and len(sp) == 2 and all(num(x) for x in sp)
    if st == "mean_sd" and num(v) and num(sp):
        return v, sp, ""
    if st == "mean_sem" and num(v) and num(sp) and num(n) and n > 0:
        return v, sp * math.sqrt(n), "SEM→SD: sd = sem × √n"
    if st == "median_iqr" and num(v) and pair and num(n) and n > 0:
        q1, q3 = sp
        mean = (q1 + v + q3) / 3
        sd = (q3 - q1) / (2 * _Z.inv_cdf((0.75 * n - 0.125) / (n + 0.25)))
        return mean, sd, "median/IQR→mean/SD (Wan et al. 2014)"
    if st == "median_range" and num(v) and pair and num(n) and n > 0:
        a, b = sp
        mean = (a + 2 * v + b) / 4
        sd = (b - a) / (2 * _Z.inv_cdf((n - 0.375) / (n + 0.25)))
        return mean, sd, "median/range→mean/SD (Wan et al. 2014)"
    if st == "mean_ci" and num(v) and pair and num(n) and n > 1:
        lo, hi = sp
        return v, math.sqrt(n) * (hi - lo) / (2 * t975(n - 1)), "95% CI→SD: sd = √n·(hi−lo)/(2·t)"
    why = f"cannot convert {st or 'unknown stat_type'}"
    if st in ("mean_sem", "median_iqr", "median_range", "mean_ci") and not num(n):
        why += " without n"
    return None, None, why


# ------------------------------------------------------------------- helpers

def effective_edges(project, findings):
    """Edges with human decisions applied; rejected edges are dropped."""
    state = load_state(project, findings)
    out = []
    for e in read_jsonl(project.edges):
        s = state.get(e["edge_key"], {})
        if s.get("status") == "rejected":
            continue
        e = dict(e, review=s.get("status", "unreviewed"))
        if s.get("type"):
            e["type"] = s["type"]
            e["polarity"] = EDGE_TYPES[s["type"]][0]
        out.append(e)
    return out, state


def components(nodes_edges: list[tuple[str, str]]) -> list[set[str]]:
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in nodes_edges:
        parent[find(a)] = find(b)
    groups = defaultdict(set)
    for x in list(parent):
        groups[find(x)].add(x)
    return sorted(groups.values(), key=lambda s: (-len(s), sorted(s)[0]))


def cite(paper: dict) -> str:
    authors = paper.get("authors") or []
    first = re.split(r"[ ,]", authors[0])[0] if authors else paper["paper_id"]
    return f"{first} {paper.get('year') or ''}".strip()


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:40] or "x"


def _md(s) -> str:
    return str(s if s is not None else "—").replace("|", "\\|").replace("\n", " ")


# ------------------------------------------------------------------- driver

def run(project) -> dict:
    project.ensure()
    findings = read_jsonl(project.findings)
    if not findings:
        raise SystemExit("litgraph report: no findings")
    corpus = {p["paper_id"]: p for p in read_json(project.corpus, [])}
    ents = {e["entity_id"]: e for e in (read_json(project.entities, {}) or {}).get("entities", [])}
    groups = {g["group_id"]: g for g in (read_json(project.entities, {}) or {}).get("groups", [])}
    cohorts = (read_json(project.cohorts, {}) or {}).get("cohorts", [])
    cfg = project.load_config()
    by_id = {f["finding_id"]: f for f in findings}
    edges, state = effective_edges(project, findings)
    paper_of = lambda fid: corpus[by_id[fid]["paper_id"]]

    def ent_name(f):
        e = ents.get(f.get("entity_id"))
        return e["canonical"] if e else f["entity"]

    def comp_name(f):
        c = f.get("comparison", {})
        name = lambda side: (groups.get(c.get(f"{side}_id")) or {}).get("canonical") or c.get(side)
        return f"{name('case')} vs {name('control')}" if c.get("control") else name("case")

    data = {"generated": dt.datetime.now().isoformat(timespec="minutes"), "pipeline_version": PIPELINE_VERSION}
    L = []

    # --- replication table
    by_ent = defaultdict(list)
    for f in findings:
        by_ent[f.get("entity_id") or "~" + f["entity"]].append(f)
    deg = defaultdict(lambda: defaultdict(int))
    for e in edges:
        for end in (e["source"], e["target"]):
            deg[end][e["type"]] += 1
    rep_rows = []
    for eid, fs in by_ent.items():
        papers = sorted({f["paper_id"] for f in fs})
        grps = sorted({corpus[p].get("author_group") for p in papers if corpus[p].get("author_group")})
        ids = {f["finding_id"] for f in fs}
        reps = sum(1 for e in edges if e["type"] == "replicates" and e["source"] in ids and e["target"] in ids)
        cons = sum(1 for e in edges if e["polarity"] == "conflicts" and (e["source"] in ids or e["target"] in ids))
        if len(papers) < 2 and not cons:
            verdict = "single report"
        elif cons and not reps:
            verdict = "conflicting"
        elif cons:
            verdict = "mixed"
        elif reps and len(grps) >= 2:
            verdict = "replicated (independent groups)"
        elif reps:
            verdict = "replicated within one group only"
        else:
            verdict = "reported by several papers, no replication edge"
        rep_rows.append({"entity": ent_name(fs[0]), "entity_id": eid, "type": (ents.get(eid) or {}).get("type") or fs[0].get("entity_type"),
                         "papers": len(papers), "findings": len(fs), "replicates": reps, "conflicts": cons,
                         "groups": len(grps), "verdict": verdict})
    rep_rows.sort(key=lambda r: (-r["papers"], -r["findings"], r["entity"]))
    data["replication"] = rep_rows

    by_type = defaultdict(lambda: [0, 0])
    for r in rep_rows:
        t = r["type"] or "other"
        by_type[t][0] += 1
        by_type[t][1] += r["papers"] >= 2
    data["overlap"] = {t: {"entities": a, "in_2plus_papers": b} for t, (a, b) in sorted(by_type.items())}

    # --- contradictions
    conflict = [e for e in edges if e["polarity"] == "conflicts"]
    confounds = [e for e in edges if e["type"] == "method_confound"]
    clusters = []
    for comp in components([(e["source"], e["target"]) for e in conflict]):
        ce = [e for e in conflict if e["source"] in comp]
        expl = [e for e in confounds if e["source"] in comp or e["target"] in comp]
        clusters.append({"findings": sorted(comp), "edges": [e["edge_id"] for e in ce],
                         "explanations": [e["edge_id"] for e in expl],
                         "qualifiers": sorted({q for e in ce for q in e.get("qualifiers", [])})})
    data["contradiction_clusters"] = clusters

    # --- independence
    groups_papers = defaultdict(list)
    for p in corpus.values():
        if p.get("author_group"):
            groups_papers[p["author_group"]].append(p["paper_id"])
    multi_groups = {g: sorted(ps) for g, ps in groups_papers.items() if len(ps) > 1}
    shared_cohorts = [c for c in cohorts if len(c.get("papers", [])) > 1]
    single_group = [r for r in rep_rows if r["papers"] >= 2 and r["groups"] == 1]
    data["independence"] = {"author_groups": multi_groups, "shared_cohorts": [c["cohort_id"] for c in shared_cohorts],
                            "single_group_entities": [r["entity_id"] for r in single_group]}

    # --- poolable sets + CSVs
    pool = [e for e in edges if e["type"] == "poolable"]
    for old in project.reports.glob("poolable_*.csv"):
        old.unlink()
    pool_sets = []
    for comp in components([(e["source"], e["target"]) for e in pool]):
        fs = sorted((by_id[x] for x in comp), key=lambda f: (paper_of(f["finding_id"]).get("year") or 0, f["finding_id"]))
        name = f"poolable_{slug(ent_name(fs[0]))}_{slug(comp_name(fs[0]).replace(' vs ', '-vs-'))}"
        path = project.reports / f"{name}.csv"
        n_used = 1
        while any(s["csv"] == str(path) for s in pool_sets):
            n_used += 1
            path = project.reports / f"{name}-{n_used}.csv"
        rows = poolable_rows(fs, corpus)
        write_csv(path, rows)
        pool_sets.append({"entity": ent_name(fs[0]), "comparison": comp_name(fs[0]), "findings": [f["finding_id"] for f in fs],
                          "stat_types": sorted({g.get("stat_type") or "unknown" for f in fs for g in (f.get("stats") or {}).get("groups", [])}),
                          "csv": str(path), "complete_rows": sum(1 for r in rows if r["sd1"] != "" and r["sd2"] != "")})
    data["poolable_sets"] = pool_sets

    # ---------------- markdown
    title = cfg.get("topic") or "litgraph synthesis"
    reviewed = sum(1 for e in edges if e["review"] in ("accepted",))
    L += [f"# Connections: {title}", "",
          f"*{len(corpus)} papers · {len(findings)} findings · {len(edges)} connections "
          f"({reviewed} accepted by a reviewer) · generated {data['generated']} · litgraph {PIPELINE_VERSION}*", "",
          NARRATIVE_START, _old_narrative(project), NARRATIVE_END, ""]

    L += ["## Replication by entity", "",
          "| Entity | Type | Papers | Findings | Replicates | Conflicts | Groups | Verdict |",
          "|---|---|---:|---:|---:|---:|---:|---|"]
    for r in rep_rows:
        if r["papers"] >= 2 or r["conflicts"]:
            L.append(f"| {_md(r['entity'])} | {_md(r['type'])} | {r['papers']} | {r['findings']} | {r['replicates']} "
                     f"| {r['conflicts']} | {r['groups']} | {r['verdict']} |")
    singles = sum(1 for r in rep_rows if r["papers"] < 2 and not r["conflicts"])
    L += ["", f"{singles} further entities were reported by a single paper only.", "", "**Overlap across papers**", ""]
    for t, o in data["overlap"].items():
        L.append(f"- {o['in_2plus_papers']} of {o['entities']} {t} entities were reported by two or more papers")
    L.append("")

    L += ["## Contradictions", ""]
    if not clusters:
        L += ["No conflicting findings were identified.", ""]
    edge_by_id = {e["edge_id"]: e for e in edges}
    for i, c in enumerate(clusters, 1):
        f0 = by_id[c["findings"][0]]
        L += [f"### {i}. {_md(ent_name(f0))} ({len(c['findings'])} findings)", ""]
        for fid in c["findings"]:
            f = by_id[fid]
            L.append(f"- **{cite(paper_of(fid))}** (`{fid}`): {_md(f['claim'])}")
        L.append("")
        for eid in c["edges"]:
            e = edge_by_id[eid]
            L.append(f"- *{e['type']}* `{e['source']}` ↔ `{e['target']}`: {_md(e['rationale'])}")
        if c["explanations"]:
            L += ["", "Candidate explanations:"]
            for eid in c["explanations"]:
                e = edge_by_id[eid]
                L.append(f"- {_md(e['rationale'])} (`{e['source']}` → `{e['target']}`, confidence {e['confidence']})")
        elif c["qualifiers"]:
            L += ["", "Differences noted between these studies: " + "; ".join(_md(q) for q in c["qualifiers"])]
        L.append("")

    L += ["## Independence warnings", ""]
    if shared_cohorts:
        L += ["**Shared cohorts**", ""]
        for c in shared_cohorts:
            L.append(f"- {_md(c.get('name'))}{' (' + c['registry_id'] + ')' if c.get('registry_id') else ''}: "
                     + ", ".join(cite(corpus[p]) for p in c["papers"] if p in corpus))
        L.append("")
    if multi_groups:
        L += ["**Papers from the same research group**", ""]
        for g, ps in sorted(multi_groups.items()):
            L.append(f"- `{g}`: " + ", ".join(cite(corpus[p]) for p in ps))
        L.append("")
    if single_group:
        L += ["**Findings reported by several papers but only one group** (no independent replication)", ""]
        for r in single_group:
            L.append(f"- {_md(r['entity'])} ({r['papers']} papers)")
        L.append("")
    if not (shared_cohorts or multi_groups or single_group):
        L += ["No shared cohorts or repeated research groups were found.", ""]

    L += ["## Poolable sets", ""]
    if not pool_sets:
        L += ["No sets of findings were judged poolable.", ""]
    for s in pool_sets:
        L.append(f"- **{_md(s['entity'])}, {_md(s['comparison'])}**: {len(s['findings'])} studies; statistics reported as "
                 f"{', '.join(s['stat_types'])}; {s['complete_rows']} rows complete after conversion → "
                 f"`{project.rel(s['csv'])}`")
    L.append("")

    flagged = [f for f in findings if f.get("flags")]
    unver = [f for f in findings if f.get("verified_quote") is False]
    L += ["## Data quality", "",
          f"- {len(unver)} findings have a quote that could not be verified on the stated page",
          f"- {len(flagged)} findings carry validation flags"]
    flag_counts = defaultdict(int)
    for f in flagged:
        for fl in f["flags"]:
            flag_counts[fl] += 1
    for fl, n in sorted(flag_counts.items(), key=lambda kv: -kv[1]):
        L.append(f"  - `{fl}`: {n}")
    stale = sum(1 for s in state.values() if s["status"] == "stale")
    if stale:
        L.append(f"- {stale} review decisions are stale because the findings changed; re-review them in the viewer")
    L.append("")

    md = project.reports / "connections.md"
    md.write_text("\n".join(L), encoding="utf-8")
    write_json(project.reports / "report_data.json", data)
    return {"connections_md": str(md), "report_data": str(project.reports / "report_data.json"),
            "contradiction_clusters": len(clusters), "poolable_sets": len(pool_sets),
            "entities_in_2plus_papers": sum(1 for r in rep_rows if r["papers"] >= 2)}


def _old_narrative(project) -> str:
    md = project.reports / "connections.md"
    if md.exists():
        text = md.read_text(encoding="utf-8")
        if NARRATIVE_START in text and NARRATIVE_END in text:
            return text.split(NARRATIVE_START, 1)[1].split(NARRATIVE_END, 1)[0].strip("\n")
    return "*Narrative summary not written yet.*"


def set_narrative(project, text: str):
    md = project.reports / "connections.md"
    body = md.read_text(encoding="utf-8")
    pre, rest = body.split(NARRATIVE_START, 1)
    _, post = rest.split(NARRATIVE_END, 1)
    md.write_text(f"{pre}{NARRATIVE_START}\n{text.strip()}\n{NARRATIVE_END}{post}", encoding="utf-8")


# ------------------------------------------------------------------- CSV

CSV_COLUMNS = ["study", "n1", "mean1", "sd1", "n2", "mean2", "sd2", "stat_type_original", "converted", "notes",
               "finding_id"]


def _pick_groups(f: dict):
    gs = (f.get("stats") or {}).get("groups") or []
    comp = f.get("comparison", {})
    def match(label):
        if not label:
            return None
        for g in gs:
            if g.get("label", "").strip().lower() == label.strip().lower():
                return g
        return None
    case, ctrl = match(comp.get("case")), match(comp.get("control"))
    if case is None and ctrl is None and len(gs) == 2:
        return gs[0], gs[1], "group order assumed (case first)"
    rest = [g for g in gs if g is not case and g is not ctrl]
    if case is None and rest:
        case = rest.pop(0)
    if ctrl is None and rest:
        ctrl = rest.pop(0)
    return case, ctrl, ""


def poolable_rows(findings: list[dict], corpus: dict) -> list[dict]:
    rows = []
    fmt = lambda x: "" if x is None else (f"{x:.6g}" if isinstance(x, float) else x)
    for f in findings:
        g1, g2, note = _pick_groups(f)
        notes = [note] if note else []
        row = {"study": cite(corpus[f["paper_id"]]), "finding_id": f["finding_id"]}
        methods, types = [], []
        for i, g in ((1, g1), (2, g2)):
            if g is None:
                row.update({f"n{i}": "", f"mean{i}": "", f"sd{i}": ""})
                notes.append(f"group {i} missing")
                continue
            m, sd, how = to_mean_sd(g)
            types.append(g.get("stat_type") or "unknown")
            if m is None:
                notes.append(f"group {i}: {how}")
            elif how:
                methods.append(how)
            row.update({f"n{i}": fmt(g.get("n")), f"mean{i}": fmt(m), f"sd{i}": fmt(sd)})
        row["stat_type_original"] = "/".join(dict.fromkeys(types))
        row["converted"] = "; ".join(dict.fromkeys(methods)) or "no"
        if any(fl in f.get("flags", []) for fl in ("implausible_spread", "mixed_sd_sem")):
            notes.append("check: spread may be SEM reported as SD")
        row["notes"] = "; ".join(notes)
        rows.append(row)
    return rows


def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in CSV_COLUMNS})
