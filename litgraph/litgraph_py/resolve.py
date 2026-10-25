"""resolve: the code half of entity resolution.

`prepare` collects every entity name, group label and cohort mention into one
file for the model to resolve. The model writes entities.json and cohorts.json.
`apply` checks those files and stamps canonical IDs onto findings and papers.

entities.json: {"entities": [{entity_id, canonical, type, aliases, related: [{entity_id, relation}]}],
                "groups":   [{group_id, canonical, aliases, parent}]}
cohorts.json:  {"cohorts":  [{cohort_id, name, aliases, registry_id, papers, evidence}]}
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict

from . import pdftext
from .io import read_json, read_jsonl, write_json, write_jsonl


def key(name: str | None) -> str:
    """Matching key for names: case, spacing, Unicode and 'hsa-' prefixes don't matter;
    arms (-3p/-5p) and isoforms do."""
    if not name:
        return ""
    s = unicodedata.normalize("NFKC", name).lower().strip()
    s = s.replace("–", "-").replace("—", "-").replace("‐", "-")
    s = re.sub(r"^(hsa|mmu|rno)-", "", s)
    s = re.sub(r"^(mirna|microrna|mi-r)[\s-]*", "mir-", s)
    s = re.sub(r"\s+", " ", s)
    return s.replace(" ", "").replace("_", "-")


def _index(items, id_field, name_fields):
    idx, clashes = {}, []
    for it in items:
        names = [it.get(f) for f in name_fields if isinstance(it.get(f), str)]
        names += list(it.get("aliases") or [])
        for n in names:
            k = key(n)
            if not k:
                continue
            if k in idx and idx[k] != it[id_field]:
                clashes.append(f"'{n}' maps to both {idx[k]} and {it[id_field]}")
            idx.setdefault(k, it[id_field])
    return idx, clashes


def load(project):
    ents = read_json(project.entities, {}) or {}
    cohorts = read_json(project.cohorts, {}) or {}
    return ents.get("entities", []), ents.get("groups", []), cohorts.get("cohorts", [])


def ancestors(groups: list[dict]) -> dict[str, list[str]]:
    """group_id -> [self, parent, grandparent, ...]"""
    parent = {g["group_id"]: g.get("parent") for g in groups}
    out = {}
    for gid in parent:
        chain, cur = [], gid
        while cur and cur not in chain:
            chain.append(cur)
            cur = parent.get(cur)
        out[gid] = chain
    return out


def annotate(project, findings: list[dict], corpus: list[dict]) -> dict:
    """Stamp entity_id / case_id / control_id onto findings and cohort_ids onto papers.
    Safe to call when nothing has been resolved yet."""
    entities, groups, cohorts = load(project)
    eidx, _ = _index(entities, "entity_id", ["canonical", "entity_id"])
    gidx, _ = _index(groups, "group_id", ["canonical", "group_id"])
    unresolved = {"entities": set(), "groups": set()}
    for f in findings:
        f["entity_id"] = eidx.get(key(f.get("entity")))
        if entities and not f["entity_id"]:
            unresolved["entities"].add(f.get("entity"))
        comp = f.setdefault("comparison", {})
        for side in ("case", "control"):
            label = comp.get(side)
            comp[f"{side}_id"] = gidx.get(key(label)) if label else None
            if groups and label and not comp[f"{side}_id"]:
                unresolved["groups"].add(label)
    by_paper = defaultdict(list)
    for c in cohorts:
        for pid in c.get("papers", []):
            by_paper[pid].append(c["cohort_id"])
    for p in corpus:
        p["cohort_ids"] = sorted(by_paper.get(p["paper_id"], []))
    return {k: sorted(v) for k, v in unresolved.items()}


def prepare(project) -> dict:
    findings = read_jsonl(project.findings)
    if not findings:
        raise SystemExit("litgraph resolve prepare: no findings yet; run `litgraph validate` first")
    entities, groups, cohorts = load(project)
    names = defaultdict(lambda: {"count": 0, "papers": set(), "types": set(), "examples": []})
    labels = defaultdict(lambda: {"count": 0, "papers": set(), "as": set()})
    for f in findings:
        n = names[f["entity"]]
        n["count"] += 1
        n["papers"].add(f["paper_id"])
        if f.get("entity_type"):
            n["types"].add(f["entity_type"])
        if len(n["examples"]) < 2:
            n["examples"].append(f"{f['finding_id']}: {f['claim']}")
        for side in ("case", "control"):
            lab = f.get("comparison", {}).get(side)
            if lab:
                labels[lab]["count"] += 1
                labels[lab]["papers"].add(f["paper_id"])
                labels[lab]["as"].add(side)

    def clusters(d):
        by = defaultdict(list)
        for name in d:
            by[key(name)].append(name)
        return [v for v in by.values() if len(v) > 1]

    out = {
        "instructions": "Write litgraph/entities.json and litgraph/cohorts.json. Every entity name and "
                        "group label below must appear as a canonical name or alias of exactly one entry. "
                        "Merge only true synonyms; link near-misses through `related` (entities) or "
                        "`parent` (groups). Keep existing IDs stable.",
        "entity_names": [{"name": k, "count": v["count"], "papers": sorted(v["papers"]),
                          "types": sorted(v["types"]), "examples": v["examples"]}
                         for k, v in sorted(names.items(), key=lambda kv: key(kv[0]))],
        "group_labels": [{"label": k, "count": v["count"], "papers": sorted(v["papers"]), "used_as": sorted(v["as"])}
                         for k, v in sorted(labels.items(), key=lambda kv: key(kv[0]))],
        "cohort_mentions": read_json(project.work / "cohort_mentions.json", []),
        "auto_clusters": {"entities": clusters(names), "groups": clusters(labels)},
        "existing": {"entities": entities, "groups": groups, "cohorts": cohorts},
    }
    path = project.work / "resolve_input.json"
    write_json(path, out)
    return {"entity_names": len(names), "group_labels": len(labels),
            "cohort_mentions": len(out["cohort_mentions"]), "input_path": str(path)}


def check(project) -> list[str]:
    """Problems with entities.json / cohorts.json that the model should fix."""
    entities, groups, cohorts = load(project)
    problems = []
    for kind, items, idf in (("entity", entities, "entity_id"), ("group", groups, "group_id"),
                             ("cohort", cohorts, "cohort_id")):
        ids = [it.get(idf) for it in items]
        dup = {i for i in ids if ids.count(i) > 1}
        problems += [f"duplicate {kind} id {i}" for i in sorted(dup, key=str)]
        problems += [f"{kind} without {idf}: {it}" for it in items if not it.get(idf)]
    eids = {e.get("entity_id") for e in entities}
    gids = {g.get("group_id") for g in groups}
    for e in entities:
        for r in e.get("related") or []:
            if r.get("entity_id") not in eids:
                problems.append(f"{e['entity_id']}: related entity {r.get('entity_id')} does not exist")
    for g in groups:
        if g.get("parent") and g["parent"] not in gids:
            problems.append(f"group {g['group_id']}: parent {g['parent']} does not exist")
    parent = {g.get("group_id"): g.get("parent") for g in groups}
    for gid in parent:
        seen, cur = set(), gid
        while cur and cur not in seen:
            seen.add(cur)
            cur = parent.get(cur)
        if cur:
            problems.append(f"group {gid}: parent chain loops")
    problems += _index(entities, "entity_id", ["canonical", "entity_id"])[1]
    problems += _index(groups, "group_id", ["canonical", "group_id"])[1]
    paper_ids = {p["paper_id"] for p in read_json(project.corpus, [])}
    for c in cohorts:
        for pid in c.get("papers", []):
            if pid not in paper_ids:
                problems.append(f"cohort {c.get('cohort_id')}: unknown paper {pid}")
    return problems


def apply(project) -> dict:
    corpus = read_json(project.corpus, [])
    findings = read_jsonl(project.findings)
    problems = check(project)
    unresolved = annotate(project, findings, corpus)

    # Quote-check cohort evidence against each paper.
    data = read_json(project.cohorts, {}) or {}
    by_id = {p["paper_id"]: p for p in corpus}
    from .validate import check_evidence
    for c in data.get("cohorts", []):
        for ev in c.get("evidence") or []:
            paper = by_id.get(ev.get("paper_id"))
            if paper:
                check_evidence([ev], pdftext.cached_pages(project, paper))
    if data:
        write_json(project.cohorts, data)

    write_jsonl(project.findings, findings)
    write_json(project.corpus, corpus)
    resolved = sum(1 for f in findings if f.get("entity_id"))
    return {"findings": len(findings), "entity_resolved": resolved,
            "unresolved_entities": unresolved["entities"], "unresolved_groups": unresolved["groups"],
            "problems": problems}
