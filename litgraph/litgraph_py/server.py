"""A localhost-only server around the viewer.

Two modes:
  hub     (start.py)       /            home page listing every graph in graphs/
                           /g/<name>/   the viewer for graphs/<name>
  single  (litgraph view)  /            the viewer for one project

Per graph it serves PDFs at a page and saves reviews and node positions to disk.
Everything is read fresh from disk on each request, so the pipeline can keep
writing while the server runs.
"""

from __future__ import annotations

import csv
import html as htmllib
import io
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from . import build, hub, report, reviews
from .io import VIEWER_DIR, Project, read_json, read_jsonl, write_json

_lock = threading.Lock()


class Site:
    def __init__(self, project: Project | None = None, graphs_dir: Path | None = None):
        self.project = project
        self.graphs_dir = Path(graphs_dir).resolve() if graphs_dir else None

    @property
    def is_hub(self):
        return self.graphs_dir is not None

    def route(self, path: str):
        """-> (project or None, base url, rest of path, redirect or None)"""
        if not self.is_hub:
            return self.project, "", path, None
        if not path.startswith("/g/"):
            return None, "", path, None
        name, sep, rest = path[3:].partition("/")
        if not hub.NAME_RE.match(name):
            return None, "", path, None
        root = (self.graphs_dir / name).resolve()
        if root.parent != self.graphs_dir or not root.is_dir():
            return None, "", path, None
        if not sep:
            return None, "", path, f"/g/{quote(name)}/"
        return Project(root), f"/g/{quote(name)}", "/" + rest, None


def make_handler(site_or_project, reviewer: str | None = None):
    site = site_or_project if isinstance(site_or_project, Site) else Site(project=site_or_project)

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

        # ------------------------------------------------------------------ GET
        def do_GET(self):
            url = urlparse(self.path)
            path = unquote(url.path)
            if site.is_hub and path in ("/", "/index.html"):
                return self._send(200, home_html(site.graphs_dir).encode("utf-8"), "text/html; charset=utf-8")
            if site.is_hub and path == "/api/graphs":
                return self._json(200, {"graphs_dir": str(site.graphs_dir), "graphs": hub.list_graphs(site.graphs_dir)})
            project, base, rest, redirect = site.route(path)
            if redirect:
                return self._send(302, b"", "text/plain", {"Location": redirect})
            if project is None:
                return self._send(404, b"not found", "text/plain")

            if rest in ("/", "/index.html", "/graph.html"):
                if not project.findings.exists():
                    return self._send(200, pending_html(project, base, site.is_hub).encode("utf-8"), "text/html; charset=utf-8")
                with _lock:
                    html = build.render(project, live=True, base=base, home="/" if site.is_hub else None)
                return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            if rest.startswith("/pdf/"):
                pid = rest[len("/pdf/"):].removesuffix(".pdf")
                paper = next((p for p in read_json(project.corpus, []) if p["paper_id"] == pid), None)
                if not paper:
                    return self._send(404, b"unknown paper", "text/plain")
                pdf = (project.root / paper["pdf_path"]).resolve()
                if project.root not in pdf.parents or not pdf.exists():
                    return self._send(404, b"pdf not found", "text/plain")
                return self._send(200, pdf.read_bytes(), "application/pdf",
                                  {"Content-Disposition": f'inline; filename="{pid}.pdf"'})
            if rest == "/report":
                md = project.reports / "connections.md"
                if not md.exists():
                    return self._send(404, b"no report yet", "text/plain")
                return self._send(200, md.read_bytes(), "text/plain; charset=utf-8")
            if rest == "/api/poolable.csv":
                ids = [x for x in parse_qs(url.query).get("ids", [""])[0].split(",") if x]
                by_id = {f["finding_id"]: f for f in read_jsonl(project.findings)}
                corpus = {p["paper_id"]: p for p in read_json(project.corpus, [])}
                rows = report.poolable_rows([by_id[i] for i in ids if i in by_id], corpus)
                buf = io.StringIO()
                w = csv.DictWriter(buf, fieldnames=report.CSV_COLUMNS)
                w.writeheader()
                for r in rows:
                    w.writerow({k: r.get(k, "") for k in report.CSV_COLUMNS})
                return self._send(200, buf.getvalue().encode("utf-8"), "text/csv; charset=utf-8",
                                  {"Content-Disposition": 'attachment; filename="poolable.csv"'})
            self._send(404, b"not found", "text/plain")

        # ----------------------------------------------------------------- POST
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
            project, _, rest, _ = site.route(urlparse(self.path).path)
            if project is None:
                return self._json(404, {"error": "unknown graph"})
            with _lock:
                if rest == "/api/review":
                    findings = read_jsonl(project.findings)
                    try:
                        ev = reviews.record(project, payload, {f["finding_id"]: f for f in findings}, reviewer)
                    except ValueError as e:
                        return self._json(400, {"error": str(e)})
                    st = reviews.load_state(project, findings).get(ev["edge_key"], {})
                    return self._json(200, {"ok": True, "event": ev, "state": st})
                if rest == "/api/positions":
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


def _inline(data) -> str:
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/").replace("<!--", "<\\!--")


def home_html(graphs_dir: Path) -> str:
    template = (VIEWER_DIR / "home.html").read_text(encoding="utf-8")
    data = {"graphs_dir": str(graphs_dir), "graphs": hub.list_graphs(graphs_dir)}
    return template.replace("__LITGRAPH_HOME__", _inline(data), 1)


def pending_html(project: Project, base: str, is_hub: bool) -> str:
    """Shown for a graph the pipeline hasn't produced findings for yet."""
    d = {k: htmllib.escape(str(v)) for k, v in hub.describe(project).items()}
    back = '<a class="back" href="/">← All graphs</a>' if is_hub else ""
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{d['topic']}</title><meta http-equiv="refresh" content="10">
<style>body{{font:14px/1.5 -apple-system,BlinkMacSystemFont,Inter,sans-serif;background:#f4f5fb;color:#171a2c;display:grid;place-items:center;min-height:100vh;margin:0;padding:16px}}
@media (prefers-color-scheme:dark){{body{{background:#0b0c16;color:#eceefb}}}}
.card{{max-width:460px;text-align:center}}.back{{color:#6d5dfc;text-decoration:none}}.muted{{opacity:.65}}</style></head>
<body><div class="card">{back}<h2>{d['topic']}</h2><p>{d['stage_label']}.</p>
<p class="muted">The graph appears here once <code>/litgraph:synthesize</code> has validated the extractions. This page refreshes itself.</p></div></body></html>"""


def _bind(handler, port):
    for p in range(port, port + 20):
        try:
            return ThreadingHTTPServer(("127.0.0.1", p), handler)
        except OSError:
            continue
    raise SystemExit(f"litgraph: no free port in {port}-{port + 19}")


def _run(httpd, open_browser, label):
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"{label} at {url}  (Ctrl+C to stop)", flush=True)
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()


def serve(project, port: int = 8765, open_browser: bool = True, reviewer: str | None = None):
    _run(_bind(make_handler(Site(project=project), reviewer), port), open_browser, "litgraph viewer")


def serve_hub(graphs_dir: Path, port: int = 8765, open_browser: bool = True, reviewer: str | None = None):
    graphs_dir = Path(graphs_dir)
    graphs_dir.mkdir(parents=True, exist_ok=True)
    print(f"graphs folder: {graphs_dir.resolve()}", flush=True)
    _run(_bind(make_handler(Site(graphs_dir=graphs_dir), reviewer), port), open_browser, "litgraph")
