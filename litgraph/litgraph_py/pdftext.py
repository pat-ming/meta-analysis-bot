"""Page-level PDF text, used for quote verification and for the extractor.

Backends, in order: pymupdf (optional dependency), poppler's `pdftotext`.
If neither is available, text is None and quotes are marked unchecked
rather than failed.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .io import read_json, write_json


def backend() -> str | None:
    try:
        import fitz  # noqa: F401
        return "pymupdf"
    except ImportError:
        pass
    if shutil.which("pdftotext"):
        return "pdftotext"
    return None


def extract_pages(pdf: Path) -> list[str] | None:
    """Return one string per page (index 0 = page 1), or None if unavailable."""
    b = backend()
    if b == "pymupdf":
        import fitz
        with fitz.open(pdf) as doc:
            return [page.get_text() for page in doc]
    if b == "pdftotext":
        r = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", str(pdf), "-"],
                           capture_output=True, timeout=120)
        if r.returncode != 0:
            return None
        pages = r.stdout.decode("utf-8", "replace").split("\f")
        if pages and not pages[-1].strip():
            pages = pages[:-1]
        return pages
    return None


def cached_pages(project, paper: dict) -> list[str] | None:
    """Pages for a corpus entry, cached in work/text/<paper_id>.json by file hash."""
    cache = project.text / f"{paper['paper_id']}.json"
    data = read_json(cache)
    if data and data.get("file_hash") == paper.get("file_hash"):
        return data["pages"]
    pdf = project.root / paper["pdf_path"]
    if not pdf.exists():
        return None
    pages = extract_pages(pdf)
    if pages is None:
        return None
    write_json(cache, {"file_hash": paper.get("file_hash"), "pages": pages}, indent=None)
    write_readable(project.text / f"{paper['paper_id']}.txt", pages)
    return pages


def write_readable(path: Path, pages: list[str]):
    """Plain-text copy with page markers, which the extractor reads to cite pages."""
    with open(path, "w", encoding="utf-8") as f:
        for i, p in enumerate(pages, 1):
            f.write(f"\n=== PAGE {i} ===\n")
            f.write(p)
