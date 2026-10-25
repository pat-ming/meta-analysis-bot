"""validate: schema-check extractions, verify quotes, flag implausible numbers,
assign stable IDs and author groups, and write findings.jsonl."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict

from . import pdftext, resolve, schema
from .ingest import unique_id
from .io import read_json, write_json, write_jsonl
from .verify import verify

QUOTE_FLAG = {"not_found": "quote_not_found", "wrong_page": "quote_wrong_page",
              "no_page": "quote_no_page", "unchecked": "quote_unchecked"}


# --------------------------------------------------------------------------- quotes

def check_evidence(evidence: list[dict], pages) -> tuple[bool | None, list[str]]:
    """Verify each quote in place. Returns (all verified?, flags)."""
    flags, statuses = [], []
    for ev in evidence:
        r = verify(ev.get("quote", ""), pages, ev.get("page"))
        ev["verified"] = r["status"]
        ev["found_page"] = r["found_page"]
        ev["match_score"] = r["score"]
        statuses.append(r["status"])
        if r["status"] in QUOTE_FLAG:
            flags.append(QUOTE_FLAG[r["status"]])
    if not statuses or all(s == "unchecked" for s in statuses):
        return None, sorted(set(flags))
    return all(s == "verified" for s in statuses), sorted(set(flags))


# ----------------------------------------------------------------------- plausibility

def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _p(p):
    if _num(p):
        return float(p)
    if isinstance(p, str):
        m = re.search(r"([<>=≤≥]?)\s*([0-9.]+(?:e-?\d+)?)", p.replace(" ", ""))
        if m:
            try:
                return float(m.group(2))
            except ValueError:
                return None
    return None


def plausibility(f: dict) -> list[str]:
    flags = []
    stats = f.get("stats") or {}
    groups = stats.get("groups") or []
    for g in groups:
        st, v, sp = g.get("stat_type"), g.get("value"), g.get("spread")
        if _num(sp) and sp < 0:
            flags.append("negative_spread")
        if st in ("mean_sd", "mean_sem") and isinstance(sp, list):
            flags.append("spread_shape_mismatch")
        if st in ("median_iqr", "median_range", "mean_ci") and _num(sp):
            flags.append("spread_shape_mismatch")
        if st == "mean_sd" and _num(v) and _num(sp) and v != 0 and 0 <= sp / abs(v) < 0.02:
            # SDs this small relative to the mean usually mean an SEM was reported.
            flags.append("implausible_spread")
        if isinstance(sp, list) and len(sp) == 2 and all(_num(x) for x in sp):
            lo, hi = sp
            if lo > hi:
                flags.append("spread_reversed")
            elif _num(v) and not (lo <= v <= hi):
                flags.append("value_outside_spread")
        if _num(g.get("n")) and g["n"] == 0:
            flags.append("zero_n")
    sem = [g for g in groups if g.get("stat_type") == "mean_sem"]
    sd = [g for g in groups if g.get("stat_type") == "mean_sd"]
    if sem and sd:
        flags.append("mixed_sd_sem")

    pop = f.get("population") or {}
    pf = pop.get("pct_female")
    if _num(pf) and not (0 <= pf <= 100):
        flags.append("pct_out_of_range")
    ns = [g.get("n") for g in groups]
    if _num(pop.get("n_total")) and ns and all(_num(n) for n in ns) and sum(ns) > pop["n_total"]:
        flags.append("n_mismatch")

    eff = stats.get("effect") or {}
    metric = (eff.get("metric") or "").lower()
    val, ci = eff.get("value"), eff.get("ci")
    if _num(val):
        if metric in ("auc", "auroc", "c-statistic", "sensitivity", "specificity") and not (0 <= val <= 1):
            flags.append("effect_out_of_range")
        if metric in ("or", "hr", "rr", "odds ratio", "hazard ratio") and val <= 0:
            flags.append("effect_out_of_range")
        if metric in ("r", "rho", "spearman", "pearson") and not (-1 <= val <= 1):
            flags.append("effect_out_of_range")
        if isinstance(ci, list) and len(ci) == 2 and all(_num(x) for x in ci) and not (min(ci) <= val <= max(ci)):
            flags.append("ci_excludes_estimate")

    p = _p(stats.get("p_value"))
    sig = f.get("significant")
    if p is not None and sig is not None:
        is_lt = isinstance(stats.get("p_value"), str) and "<" in stats["p_value"]
        if sig and p > 0.05 and not is_lt:
            flags.append("p_significance_mismatch")
        if sig is False and p < 0.05:
            flags.append("p_significance_mismatch")
    if f.get("finding_type") == "null_result" and sig:
        flags.append("null_result_marked_significant")
    return sorted(set(flags))


# ------------------------------------------------------------------- author groups

def author_key(name: str) -> str:
    """'Lättekivi F.' -> 'lattekivi f'; tolerates 'F. Lättekivi' too."""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"[^a-z\s-]", " ", s)
    parts = [p for p in s.split() if p]
    if not parts:
        return ""
    if len(parts[-1]) <= 2 and len(parts) > 1:      # "Surname Initials"
        surname, init = " ".join(parts[:-1]), parts[-1][0]
    elif len(parts[0]) <= 2 and len(parts) > 1:     # "Initials Surname"
        surname, init = parts[-1], parts[0][0]
    else:                                           # "Given Surname"
        surname, init = parts[-1], parts[0][0]
    return f"{surname} {init}"


def author_groups(corpus: list[dict]) -> dict[str, str]:
    """Papers sharing a last author, or two or more authors, are one research group."""
    parent = {p["paper_id"]: p["paper_id"] for p in corpus}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    keys = {p["paper_id"]: [author_key(a) for a in p.get("authors") or [] if author_key(a)] for p in corpus}
    ids = list(keys)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            ka, kb = keys[a], keys[b]
            if not ka or not kb:
                continue
            if ka[-1] == kb[-1] or len(set(ka) & set(kb)) >= 2:
                parent[find(a)] = find(b)

    members = defaultdict(list)
    for pid in ids:
        members[find(pid)].append(pid)
    out, used = {}, set()
    for root in sorted(members, key=lambda r: sorted(members[r])[0]):
        papers = members[root]
        counts = Counter(k for pid in papers for k in set(keys[pid]))
        if counts:
            # Name the group after its most frequent author, preferring last authors on ties.
            lasts = Counter(keys[pid][-1] for pid in papers if keys[pid])
            top = max(counts, key=lambda k: (counts[k], lasts.get(k, 0), k))
            base = "group-" + re.sub(r"[^a-z]+", "", top.split(" ")[0])
        else:
            base = "group-unknown"
        gid = unique_id(base, used)
        used.add(gid)
        for pid in papers:
            out[pid] = gid
    return out


# --------------------------------------------------------------------------- driver

def _finalize_id(project, entry: dict, ext: dict, taken: set[str]) -> str | None:
    """Swap a provisional paper_id for <firstauthor><year> once we know it."""
    if not entry.get("id_provisional"):
        return None
    paper = ext.get("paper", {})
    new = paper.get("suggested_paper_id")
    if not new:
        authors, year = paper.get("authors") or [], paper.get("year")
        if authors and year:
            new = re.sub(r"[^a-z]", "", author_key(authors[0]).split(" ")[0]) + str(year)
    if not new or new == entry["paper_id"]:
        entry["id_provisional"] = False
        return None
    old = entry["paper_id"]
    new = unique_id(new, taken - {old})
    for suffix in (".json", ".txt"):
        src = project.text / f"{old}{suffix}"
        if src.exists():
            src.rename(project.text / f"{new}{suffix}")
    (project.extractions / f"{old}.json").unlink(missing_ok=True)
    ext["paper_id"] = new
    for f in ext.get("findings", []):
        if f.get("finding_id", "").startswith(old + "-"):
            f["finding_id"] = new + f["finding_id"][len(old):]
    write_json(project.extractions / f"{new}.json", ext)
    entry["paper_id"] = new
    entry["id_provisional"] = False
    return new


def check_extraction(ext: dict) -> list[str]:
    errs = schema.errors(ext, "extraction.schema.json")
    seen = Counter(f.get("finding_id") for f in ext.get("findings", []) if isinstance(f, dict))
    errs += [f"duplicate finding_id {k}" for k, n in seen.items() if k and n > 1]
    pid = ext.get("paper_id")
    for i, f in enumerate(ext.get("findings", [])):
        if isinstance(f, dict) and pid and not str(f.get("finding_id", "")).startswith(pid + "-f"):
            errs.append(f"$.findings[{i}].finding_id: must start with '{pid}-f'")
    return errs


def run(project) -> dict:
    corpus = read_json(project.corpus, [])
    if not corpus:
        raise SystemExit("litgraph validate: corpus.json is empty; run `litgraph ingest` first")
    report = {"papers": {}, "quote_failures": []}
    findings, cohort_mentions, baselines = [], [], {}
    taken = {p["paper_id"] for p in corpus}

    for entry in corpus:
        pid = entry["paper_id"]
        ext = read_json(project.extractions / f"{pid}.json")
        if ext is None:
            report["papers"][pid] = {"status": "missing"}
            continue
        if ext.get("file_hash") != entry["file_hash"]:
            report["papers"][pid] = {"status": "stale", "detail": "PDF changed since extraction"}
            continue
        errs = check_extraction(ext)
        if errs:
            report["papers"][pid] = {"status": "invalid", "errors": errs[:50]}
            continue
        new = _finalize_id(project, entry, ext, taken)
        if new:
            taken.discard(pid)
            taken.add(new)
            pid = new

        paper = ext["paper"]
        for k in ("title", "authors", "year", "journal", "doi", "pmid", "study_design"):
            if paper.get(k) or k not in entry:
                entry[k] = paper.get(k) or entry.get(k)
        pages = pdftext.cached_pages(project, entry)

        n_flagged = n_ver = 0
        for f in ext["findings"]:
            f["paper_id"] = pid
            ok, qflags = check_evidence(f["evidence"], pages)
            f["verified_quote"] = ok
            f["flags"] = sorted(set(qflags) | set(plausibility(f)))
            n_ver += ok is True
            n_flagged += bool(f["flags"])
            if ok is False:
                report["quote_failures"].append({
                    "finding_id": f["finding_id"],
                    "problems": [{"page": e.get("page"), "status": e["verified"], "found_page": e.get("found_page"),
                                  "quote": e["quote"][:160]} for e in f["evidence"] if e["verified"] != "verified"]})
            findings.append(f)
        for c in ext.get("cohorts") or []:
            check_evidence(c["evidence"], pages)
            cohort_mentions.append(dict(c, paper_id=pid))
        if ext.get("baseline"):
            baselines[pid] = ext["baseline"]
        report["papers"][pid] = {"status": "ok", "findings": len(ext["findings"]),
                                 "verified": n_ver, "flagged": n_flagged}

    groups = author_groups(corpus)
    for entry in corpus:
        entry["author_group"] = groups.get(entry["paper_id"])
    write_json(project.corpus, corpus)

    resolve.annotate(project, findings, corpus)  # keep earlier entity resolution attached
    findings.sort(key=lambda f: (f["paper_id"], int(f["finding_id"].rsplit("-f", 1)[1])))
    write_jsonl(project.findings, findings)
    write_json(project.work / "cohort_mentions.json", cohort_mentions)
    write_json(project.work / "baselines.json", baselines)

    statuses = Counter(p["status"] for p in report["papers"].values())
    flags = Counter(fl for f in findings for fl in f["flags"])
    checked = [f for f in findings if f["verified_quote"] is not None]
    summary = {
        "papers": dict(statuses),
        "findings": len(findings),
        "quotes_verified": f"{sum(f['verified_quote'] is True for f in checked)}/{len(checked)}",
        "flags": dict(flags.most_common()),
        "report_path": str(project.work / "validation_report.json"),
    }
    report["summary"] = summary
    write_json(project.work / "validation_report.json", report)
    return summary
