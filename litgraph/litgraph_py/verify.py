"""Fuzzy quote verification against page text.

A quote is verified only if it is found on the page it claims (a quote may run
onto the following page). Matching tolerates hyphenation, line breaks,
ligatures, curly quotes, dash variants and ellipses for omitted text.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

THRESHOLD = 0.88  # token-level similarity needed to call a quote found
_ELLIPSIS = re.compile(r"\s*(?:\.\s*\.\s*\.|…|\[\s*\.\.\.\s*\])\s*")
_HYPHEN_BREAK = re.compile(r"(\w)[-­‐‑]\s*\n\s*(\w)")
_NON_WORD = re.compile(r"[^0-9a-z]+")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("­", "")
    text = _HYPHEN_BREAK.sub(r"\1\2", text)
    text = text.lower()
    # Greek letters are common in biomedical text; keep them as words.
    greek = {"α": " alpha ", "β": " beta ", "γ": " gamma ", "δ": " delta ", "κ": " kappa ",
             "µ": " micro ", "μ": " micro ", "±": " pm ", "≥": " ge ", "≤": " le "}
    for k, v in greek.items():
        text = text.replace(k, v)
    return _NON_WORD.sub(" ", text).strip()


def _tokens(text: str) -> list[str]:
    return normalize(text).split()


def _segment_score(seg: list[str], page: list[str], page_str: str) -> float:
    if not seg:
        return 1.0
    if " ".join(seg) in page_str:
        return 1.0
    k = len(seg)
    if len(page) < max(3, k // 2):
        return 0.0
    first = set(seg[: max(1, min(4, k))])
    best = 0.0
    sm = SequenceMatcher(autojunk=False)
    sm.set_seq2(seg)
    # Only consider windows that start near a token the quote begins with.
    starts = {max(0, i - 2) for i, t in enumerate(page) if t in first} or {0}
    for s in starts:
        window = page[s: s + k + 4]
        sm.set_seq1(window)
        # quick_ratio bounds 2M/(k+w); turn it into a bound on M/k to prune.
        if sm.quick_ratio() * (k + len(window)) / (2 * k) < best:
            continue
        # Score as the share of quote tokens matched, so extra window tokens don't penalise.
        matched = sum(b.size for b in sm.get_matching_blocks())
        best = max(best, matched / k)
        if best >= 0.999:
            break
    return best


def score_on(quote: str, page_text: str) -> float:
    """Similarity in [0,1] of `quote` to its best match inside `page_text`."""
    page = _tokens(page_text)
    page_str = " ".join(page)
    segments = [_tokens(s) for s in _ELLIPSIS.split(quote)]
    segments = [s for s in segments if s]
    if not segments:
        return 0.0
    total = sum(len(s) for s in segments)
    return sum(_segment_score(s, page, page_str) * len(s) for s in segments) / total


def verify(quote: str, pages: list[str] | None, page: int | None) -> dict:
    """Check one quote. Returns {"status", "score", "found_page"}.

    status is one of: verified, wrong_page, not_found, unchecked (no text), no_page.
    """
    if pages is None:
        return {"status": "unchecked", "score": None, "found_page": None}
    if not quote or not quote.strip():
        return {"status": "not_found", "score": 0.0, "found_page": None}
    n = len(pages)
    if page is not None and 1 <= page <= n:
        s = score_on(quote, pages[page - 1])
        if s < THRESHOLD and page < n:
            # A quote may start on this page and run onto the next, but one that sits
            # entirely on the next page is cited to the wrong page.
            joined = score_on(quote, pages[page - 1] + "\n" + pages[page])
            if joined >= THRESHOLD and score_on(quote, pages[page]) < THRESHOLD:
                s = joined
        if s >= THRESHOLD:
            return {"status": "verified", "score": round(s, 3), "found_page": page}
    # Look elsewhere, to tell the extractor where it actually is.
    best_page, best = None, 0.0
    for i, text in enumerate(pages, 1):
        if i == page:
            continue
        s = score_on(quote, text)
        if s > best:
            best_page, best = i, s
    if best >= THRESHOLD:
        status = "wrong_page" if page is not None else "no_page"
        return {"status": status, "score": round(best, 3), "found_page": best_page}
    return {"status": "not_found", "score": round(best, 3), "found_page": None}
