"""ingest: list PDFs, hash them, detect DOI/PMID, and decide what needs extracting."""

from __future__ import annotations

import re
from pathlib import Path

from . import pdftext
from .io import read_json, sha256_file, write_json

DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>,;]+)", re.I)
PMID_RE = re.compile(r"\bPMID\s*:?\s*(\d{6,9})\b", re.I)
PMID_FILENAME_RE = re.compile(r"^(?:pmid[-_ ]?)?(\d{7,9})$", re.I)
ID_FILENAME_RE = re.compile(r"^([a-z]+)[-_ ]?(\d{4})([a-z]?)$", re.I)


def slug(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s or "paper"


def detect_ids(pages: list[str] | None, filename: str) -> dict:
    out = {"doi": None, "pmid": None}
    stem = Path(filename).stem
    m = PMID_FILENAME_RE.match(stem)
    if m:
        out["pmid"] = m.group(1)
    # A DOI in the file name, with '/' written as '_' (common when saving downloads).
    m = re.match(r"^(10\.\d{4,9})[_](.+)$", stem)
    if m:
        out["doi"] = f"{m.group(1)}/{m.group(2)}"
    if pages:
        head = "\n".join(pages[:2])
        if not out["doi"]:
            m = DOI_RE.search(head)
            if m:
                out["doi"] = m.group(1).rstrip(".)]")
        if not out["pmid"]:
            m = PMID_RE.search(head)
            if m:
                out["pmid"] = m.group(1)
    return out


def provisional_id(pdf: Path, taken: set[str]) -> tuple[str, bool]:
    """(paper_id, provisional?). `smith2021.pdf` is final; anything else is
    provisional until the extractor suggests `<firstauthor><year>`."""
    m = ID_FILENAME_RE.match(pdf.stem)
    if m:
        base, final = f"{m.group(1).lower()}{m.group(2)}{m.group(3).lower()}", True
    else:
        base, final = slug(pdf.stem)[:40], False
    return unique_id(base, taken), not final


def unique_id(base: str, taken: set[str]) -> str:
    if base not in taken:
        return base
    for suffix in "bcdefghijklmnopqrstuvwxyz":
        if f"{base}{suffix}" not in taken:
            return f"{base}{suffix}"
    i = 2
    while f"{base}-{i}" in taken:
        i += 1
    return f"{base}-{i}"


def run(project, papers_dir: str = "papers") -> dict:
    project.ensure()
    src = (project.root / papers_dir).resolve()
    if not src.is_dir():
        raise SystemExit(f"litgraph ingest: no such folder: {src}")
    pdfs = sorted(p for p in src.rglob("*") if p.suffix.lower() == ".pdf" and not p.name.startswith("."))

    old = {p["paper_id"]: p for p in read_json(project.corpus, [])}
    by_path = {p["pdf_path"]: p for p in old.values()}
    by_hash = {p["file_hash"]: p for p in old.values()}
    taken: set[str] = set()
    corpus, todo, cached, changed = [], [], [], []

    for pdf in pdfs:
        rel = project.rel(pdf)
        h = sha256_file(pdf)
        prev = by_path.get(rel) or by_hash.get(h)
        if prev and prev["paper_id"] not in taken:
            entry = dict(prev, pdf_path=rel)
            if prev["file_hash"] != h:
                changed.append(prev["paper_id"])
                entry["file_hash"] = h
        else:
            pid, provisional = provisional_id(pdf, taken | set(old))
            entry = {"paper_id": pid, "pdf_path": rel, "file_hash": h,
                     "id_provisional": provisional}
        taken.add(entry["paper_id"])

        pages = pdftext.cached_pages(project, entry)
        entry["n_pages"] = len(pages) if pages is not None else None
        for k, v in detect_ids(pages, pdf.name).items():
            if v and not entry.get(k):
                entry[k] = v

        ext = read_json(project.extractions / f"{entry['paper_id']}.json")
        if ext and ext.get("file_hash") == h:
            cached.append(entry["paper_id"])
        else:
            txt = project.text / f"{entry['paper_id']}.txt"
            todo.append({
                "paper_id": entry["paper_id"],
                "pdf_path": str(pdf),
                "text_path": str(txt) if txt.exists() else None,
                "file_hash": h,
                "n_pages": entry["n_pages"],
                "out_path": str(project.extractions / f"{entry['paper_id']}.json"),
            })
        corpus.append(entry)

    removed = sorted(set(old) - taken)
    write_json(project.corpus, corpus)
    summary = {
        "papers": len(corpus),
        "to_extract": len(todo),
        "cached": len(cached),
        "changed": changed,
        "removed": removed,
        "text_backend": pdftext.backend(),
        "todo_path": str(project.work / "todo.json"),
    }
    write_json(project.work / "todo.json", {"project": str(project.root), "papers": todo})
    return summary
