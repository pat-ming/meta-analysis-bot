"""view: a localhost-only server around the viewer. It adds what a static file
can't do: serve PDFs at a page, and save reviews and node positions to disk."""

from __future__ import annotations

import json
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from . import build, report, reviews
from .io import read_json, read_jsonl, write_json

_lock = threading.Lock()


def make_handler(project, reviewer: str | None):
    class Handler(BaseHTTPRequestHandler):
        server_version = "litgraph"

        def log_message(self, fmt, *args):  # keep the terminal quiet
            pass

        def _send(self, code, body: bytes, ctype: str, extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code, obj):
            self._send(code, json.dumps(obj).encode(), "application/json")

        def _same_origin(self) -> bool:
            # Block other websites from posting to localhost (CSRF).
            origin = self.headers.get("Origin")
            host = self.headers.get("Host", "")
            return origin is None or urlparse(origin).netloc == host

        def do_GET(self):
            url = urlparse(self.path)
            path = unquote(url.path)
            if path in ("/", "/index.html", "/graph.html"):
                with _lock:
                    html = build.render(project, live=True)
                return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            if path.startswith("/pdf/"):
                pid = path[len("/pdf/"):].removesuffix(".pdf")
                paper = next((p for p in read_json(project.corpus, []) if p["paper_id"] == pid), None)
                if not paper:
                    return self._send(404, b"unknown paper", "text/plain")
                pdf = (project.root / paper["pdf_path"]).resolve()
                if project.root not in pdf.parents or not pdf.exists():
                    return self._send(404, b"pdf not found", "text/plain")
                return self._send(200, pdf.read_bytes(), "application/pdf",
                                  {"Content-Disposition": f'inline; filename="{pid}.pdf"'})
            if path == "/api/poolable.csv":
                ids = [x for x in parse_qs(url.query).get("ids", [""])[0].split(",") if x]
                by_id = {f["finding_id"]: f for f in read_jsonl(project.findings)}
                corpus = {p["paper_id"]: p for p in read_json(project.corpus, [])}
                rows = report.poolable_rows([by_id[i] for i in ids if i in by_id], corpus)
                import csv
                import io
                buf = io.StringIO()
                w = csv.DictWriter(buf, fieldnames=report.CSV_COLUMNS)
                w.writeheader()
                for r in rows:
                    w.writerow({k: r.get(k, "") for k in report.CSV_COLUMNS})
                return self._send(200, buf.getvalue().encode("utf-8"), "text/csv; charset=utf-8",
                                  {"Content-Disposition": 'attachment; filename="poolable.csv"'})
            self._send(404, b"not found", "text/plain")

        def do_POST(self):
            if not self._same_origin():
                return self._json(403, {"error": "cross-origin request refused"})
            if "application/json" not in self.headers.get("Content-Type", ""):
                return self._json(415, {"error": "expected application/json"})
            try:
                n = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(min(n, 5_000_000)) or b"{}")
            except (ValueError, json.JSONDecodeError):
                return self._json(400, {"error": "bad json"})
            path = urlparse(self.path).path
            with _lock:
                if path == "/api/review":
                    findings = read_jsonl(project.findings)
                    try:
                        ev = reviews.record(project, payload, {f["finding_id"]: f for f in findings}, reviewer)
                    except ValueError as e:
                        return self._json(400, {"error": str(e)})
                    st = reviews.load_state(project, findings).get(ev["edge_key"], {})
                    return self._json(200, {"ok": True, "event": ev, "state": st})
                if path == "/api/positions":
                    pos = read_json(project.positions, {}) or {}
                    for k, v in (payload.get("positions") or {}).items():
                        if isinstance(v, dict) and all(isinstance(v.get(a), (int, float)) for a in ("x", "y")):
                            pos[k] = {"x": round(v["x"], 1), "y": round(v["y"], 1)}
                    if payload.get("reset"):
                        pos = {}
                    write_json(project.positions, pos)
                    return self._json(200, {"ok": True, "saved": len(pos)})
            self._json(404, {"error": "not found"})

    return Handler


def serve(project, port: int = 8765, open_browser: bool = True, reviewer: str | None = None):
    handler = make_handler(project, reviewer)
    httpd = None
    for p in range(port, port + 20):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", p), handler)
            break
        except OSError:
            continue
    if httpd is None:
        raise SystemExit(f"litgraph view: no free port in {port}-{port + 19}")
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"litgraph viewer at {url}  (Ctrl+C to stop)", flush=True)
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
