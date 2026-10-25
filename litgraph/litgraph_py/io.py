"""Project layout and small file helpers."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = PLUGIN_ROOT / "schemas"
VIEWER_DIR = PLUGIN_ROOT / "viewer"


class Project:
    """Paths inside a review project (the folder that holds papers/ and litgraph/)."""

    def __init__(self, root: str | os.PathLike = "."):
        self.root = Path(root).resolve()
        self.dir = self.root / "litgraph"

    # machine output
    @property
    def config(self): return self.dir / "config.json"
    @property
    def corpus(self): return self.dir / "corpus.json"
    @property
    def extractions(self): return self.dir / "extractions"
    @property
    def findings(self): return self.dir / "findings.jsonl"
    @property
    def entities(self): return self.dir / "entities.json"
    @property
    def cohorts(self): return self.dir / "cohorts.json"
    @property
    def edges(self): return self.dir / "edges.jsonl"
    @property
    def reports(self): return self.dir / "reports"
    @property
    def graph(self): return self.dir / "graph.html"

    # human output (written by the viewer)
    @property
    def reviews(self): return self.dir / "reviews.jsonl"
    @property
    def positions(self): return self.dir / "positions.json"

    # scratch space shared between pipeline stages
    @property
    def work(self): return self.dir / "work"
    @property
    def text(self): return self.work / "text"
    @property
    def candidates(self): return self.work / "candidates"
    @property
    def judged(self): return self.work / "judged"
    @property
    def judge_cache(self): return self.work / "judge_cache.json"

    def ensure(self):
        for d in (self.dir, self.extractions, self.reports, self.work, self.text,
                  self.candidates, self.judged):
            d.mkdir(parents=True, exist_ok=True)
        return self

    def rel(self, p: Path) -> str:
        try:
            return str(Path(p).resolve().relative_to(self.root))
        except ValueError:
            return str(p)

    def load_config(self) -> dict:
        return read_json(self.config, {})


def read_json(path: Path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data, indent=2):
    """Atomic write so a crash never leaves a half-written project file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=indent, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def read_jsonl(path: Path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def write_jsonl(path: Path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def append_jsonl(path: Path, row: dict):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def content_hash(obj) -> str:
    """Short hash of a JSON-able object, used to notice when a finding changed."""
    s = json.dumps(obj, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]
