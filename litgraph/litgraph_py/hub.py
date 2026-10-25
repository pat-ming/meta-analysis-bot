"""graphs/: one folder per systematic review. Creating graphs from a zip or a
folder of PDFs, and summarising every graph for the home page."""

from __future__ import annotations

import datetime as dt
import hashlib
import re
import zipfile
from pathlib import Path

from . import pdftext
from .edges import EDGE_TYPES
from .io import PLUGIN_ROOT, Project, read_json, read_jsonl, write_json

MAX_PDF_BYTES = 500 * 1024 * 1024        # per file, guards against zip bombs
MAX_TOTAL_BYTES = 8 * 1024 * 1024 * 1024
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")


def default_graphs_dir() -> Path:
    """graphs/ beside start.py when the plugin runs from the repo, else ./graphs."""
    repo = PLUGIN_ROOT.parent
    if (repo / "start.py").exists():
        return repo / "graphs"
    return Path.cwd() / "graphs"


def graph_name(source: str) -> str:
    stem = Path(source.rstrip("/")).name
    if stem.lower().endswith(".zip"):
        stem = stem[:-4]
    name = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")[:80]
    return name or "review"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _incoming(source: Path):
    """Yield (filename, bytes) for every PDF in a zip or folder, skipping OS junk."""
    def junk(name: str) -> bool:
        parts = Path(name).parts
        return any(p == "__MACOSX" or p.startswith(".") for p in parts)

    if source.is_dir():
        for p in sorted(source.rglob("*")):
            if p.is_file() and p.suffix.lower() == ".pdf" and not junk(str(p.relative_to(source))):
                if p.stat().st_size > MAX_PDF_BYTES:
                    raise SystemExit(f"litgraph new: {p.name} is larger than {MAX_PDF_BYTES >> 20} MB")
                yield p.name, p.read_bytes()
        return
    if not zipfile.is_zipfile(source):
        raise SystemExit(f"litgraph new: {source} is not a zip file or a folder")
    total = 0
    with zipfile.ZipFile(source) as zf:
        for info in sorted(zf.infolist(), key=lambda i: i.filename):
            if info.is_dir() or junk(info.filename) or not info.filename.lower().endswith(".pdf"):
                continue
            if info.file_size > MAX_PDF_BYTES:
                raise SystemExit(f"litgraph new: {info.filename} is larger than {MAX_PDF_BYTES >> 20} MB")
            total += info.file_size
            if total > MAX_TOTAL_BYTES:
                raise SystemExit("litgraph new: zip expands to more than 8 GB")
            # Only the base name is used, so entries can't write outside papers/.
            yield Path(info.filename.replace("\\", "/")).name, zf.read(info)


def new(source: str, name: str | None = None, graphs_dir: str | None = None, topic: str | None = None) -> dict:
    src = Path(source).expanduser().resolve()
    if not src.exists():
        raise SystemExit(f"litgraph new: no such file or folder: {src}")
    gdir = Path(graphs_dir).expanduser().resolve() if graphs_dir else default_graphs_dir()
    name = graph_name(name or src.name)
    if not NAME_RE.match(name):
        raise SystemExit(f"litgraph new: invalid graph name {name!r}")
    root = gdir / name
    papers = root / "papers"
    existed = (root / "litgraph").exists()
    papers.mkdir(parents=True, exist_ok=True)

    have = {p.name: _sha(p.read_bytes()) for p in papers.glob("*.pdf")}
    have_hashes = set(have.values())
    added, updated, unchanged, renamed = [], [], [], []
    seen_in_source: dict[str, str] = {}
    for fname, data in _incoming(src):
        h = _sha(data)
        if not data.startswith(b"%PDF"):
            continue
        if fname in seen_in_source and seen_in_source[fname] != h:
            # Two different PDFs with one name inside the upload: keep both.
            stem, i = Path(fname).stem, 2
            while f"{stem}-{i}.pdf" in seen_in_source or f"{stem}-{i}.pdf" in have:
                i += 1
            renamed.append(f"{fname} -> {stem}-{i}.pdf")
            fname = f"{stem}-{i}.pdf"
        seen_in_source[fname] = h
        if have.get(fname) == h or (fname not in have and h in have_hashes):
            unchanged.append(fname)
            continue
        (papers / fname).write_bytes(data)
        (updated if fname in have else added).append(fname)
        have[fname] = h
        have_hashes.add(h)

    project = Project(root).ensure()
    cfg = project.load_config()
    if topic or "topic" not in cfg:
        cfg["topic"] = topic or name.replace("-", " ")
    cfg.setdefault("created", dt.datetime.now().isoformat(timespec="seconds"))
    cfg["source"] = str(src)
    write_json(project.config, cfg)
    return {"graph": name, "project": str(root), "papers_dir": str(papers), "existing_graph": existed,
            "added": len(added), "updated": updated, "unchanged": len(unchanged), "renamed": renamed,
            "total_pdfs": len(list(papers.glob("*.pdf"))), "text_backend": pdftext.backend()}


# --------------------------------------------------------------------------- listing

def stage(s: dict) -> tuple[str, str]:
    """(stage id, human label) for a project summary."""
    if not s["papers"]:
        return "new", "No papers ingested yet"
    if s["extracted"] < s["papers"]:
        return "extracting", f"Extracting papers ({s['extracted']}/{s['papers']})"
    if not s["findings"]:
        return "validating", "Validating extractions"
    if not s["edges"]:
        return "connecting", "Connecting findings"
    if not s["report"]:
        return "reporting", "Writing report"
    return "ready", "Ready"


def summary(project: Project) -> dict:
    corpus = read_json(project.corpus, []) or []
    findings = read_jsonl(project.findings)
    exts = {}
    if project.extractions.exists():
        for x in project.extractions.glob("*.json"):
            try:
                exts[x.stem] = (read_json(x) or {}).get("file_hash")
            except ValueError:
                exts[x.stem] = None
    edges = read_jsonl(project.edges)
    s = {
        "papers": len(corpus),
        "extracted": sum(1 for c in corpus if exts.get(c["paper_id"]) == c["file_hash"]),
        "findings": len(findings),
        "entities_resolved": sum(1 for f in findings if f.get("entity_id")),
        "edges": len(edges),
        "reviews": len(read_jsonl(project.reviews)),
        "report": (project.reports / "connections.md").exists(),
        "graph": project.graph.exists(),
    }
    return s, corpus, findings, edges


def describe(project: Project) -> dict:
    from .reviews import load_state
    s, corpus, findings, edges = summary(project)
    cfg = project.load_config()
    state = load_state(project, findings) if edges else {}
    by_pol = {p: 0 for p in ("supports", "conflicts", "caution", "neutral")}
    reviewed = accepted = rejected = 0
    for e in edges:
        st = state.get(e["edge_key"], {})
        t = st.get("type") or e["type"]
        if st.get("status") == "rejected":
            rejected += 1
            reviewed += 1
            continue
        if st.get("status") == "accepted":
            accepted += 1
            reviewed += 1
        by_pol[EDGE_TYPES.get(t, (e.get("polarity", "neutral"),))[0]] += 1
    checked = [f for f in findings if f.get("verified_quote") is not None]
    files = [p for p in project.dir.rglob("*") if p.is_file() and "work" not in p.relative_to(project.dir).parts[:1]] \
        if project.dir.exists() else []
    updated = max((p.stat().st_mtime for p in files), default=project.root.stat().st_mtime)
    st_id, st_label = stage(s)
    years = sorted(c.get("year") for c in corpus if isinstance(c.get("year"), int))
    return {
        "name": project.root.name, "topic": cfg.get("topic") or project.root.name, "path": str(project.root),
        **s, "stage": st_id, "stage_label": st_label, "polarity": by_pol,
        "reviewed": reviewed, "accepted": accepted, "rejected": rejected,
        "quotes_verified": round(sum(1 for f in checked if f["verified_quote"]) / len(checked), 3) if checked else None,
        "entities": len({f.get("entity_id") or f.get("entity") for f in findings}),
        "years": [years[0], years[-1]] if years else None,
        "updated": dt.datetime.fromtimestamp(updated).isoformat(timespec="minutes"),
    }


def list_graphs(graphs_dir: Path) -> list[dict]:
    out = []
    if not graphs_dir.exists():
        return out
    for d in sorted(graphs_dir.iterdir()):
        if d.is_dir() and NAME_RE.match(d.name) and ((d / "litgraph").is_dir() or (d / "papers").is_dir()):
            try:
                out.append(describe(Project(d)))
            except Exception as e:  # one broken graph shouldn't hide the others
                out.append({"name": d.name, "topic": d.name, "stage": "error", "stage_label": f"Unreadable: {e}",
                            "path": str(d), "papers": 0, "findings": 0, "edges": 0, "updated": None})
    out.sort(key=lambda g: g.get("updated") or "", reverse=True)
    return out

