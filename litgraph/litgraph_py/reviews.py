"""Human review decisions. reviews.jsonl is an append-only log written by the
viewer; the latest event per edge_key (source|target|type) wins.

A decision goes stale when either finding it was made about has since changed,
so a re-extraction can't silently inherit an old accept or reject.
"""

from __future__ import annotations

import datetime as dt
import getpass

from .edges import EDGE_TYPES, finding_hash
from .io import append_jsonl, read_jsonl

DECISIONS = ("accept", "reject", "retype", "note", "reset")


def fold(events: list[dict], hashes: dict[str, str]) -> dict[str, dict]:
    state: dict[str, dict] = {}
    for ev in events:
        k = ev.get("edge_key")
        if not k:
            continue
        s = state.setdefault(k, {"status": "unreviewed", "type": None, "notes": [], "reviewer": None,
                                 "timestamp": None, "finding_hashes": None})
        d = ev.get("decision")
        if d == "accept":
            s["status"] = "accepted"
        elif d == "reject":
            s["status"] = "rejected"
        elif d == "retype" and ev.get("new_type") in EDGE_TYPES:
            s["type"], s["status"] = ev["new_type"], "accepted"
        elif d == "reset":
            s.update(status="unreviewed", type=None)
        if ev.get("note"):
            s["notes"].append({"text": ev["note"], "reviewer": ev.get("reviewer"), "timestamp": ev.get("timestamp")})
        if d != "note":
            s["reviewer"], s["timestamp"] = ev.get("reviewer"), ev.get("timestamp")
            s["finding_hashes"] = ev.get("finding_hashes")
    for k, s in state.items():
        fh = s.get("finding_hashes") or {}
        if s["status"] in ("accepted", "rejected") and fh and any(hashes.get(f) != h for f, h in fh.items()):
            s["stale_status"] = s["status"]
            s["status"] = "stale"
    return state


def load_state(project, findings: list[dict]) -> dict[str, dict]:
    return fold(read_jsonl(project.reviews), {f["finding_id"]: finding_hash(f) for f in findings})


def record(project, payload: dict, findings_by_id: dict, reviewer: str | None = None) -> dict:
    d = payload.get("decision")
    if d not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS}")
    k = payload.get("edge_key") or ""
    parts = k.split("|")
    if len(parts) != 3:
        raise ValueError("edge_key must be source|target|type")
    if d == "retype" and payload.get("new_type") not in EDGE_TYPES:
        raise ValueError(f"new_type must be one of {sorted(EDGE_TYPES)}")
    ev = {
        "edge_key": k,
        "edge_id": payload.get("edge_id"),
        "decision": d,
        "new_type": payload.get("new_type"),
        "note": (payload.get("note") or "").strip() or None,
        "reviewer": payload.get("reviewer") or reviewer or getpass.getuser(),
        "finding_hashes": {f: finding_hash(findings_by_id[f]) for f in parts[:2] if f in findings_by_id},
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }
    append_jsonl(project.reviews, ev)
    return ev
