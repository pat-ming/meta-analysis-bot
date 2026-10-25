"""Run with:  python3 -m unittest discover -s tests -v"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "litgraph"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fixture  # noqa: E402
from litgraph_py import cli, pdftext, report, schema, validate  # noqa: E402
from litgraph_py.io import Project, read_json, read_jsonl, write_json  # noqa: E402
from litgraph_py.verify import verify  # noqa: E402


def run_cli(project, *args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.main(["-C", str(project), *args])
    return code, json.loads(buf.getvalue())


class TestVerify(unittest.TestCase):
    pages = ["Intro text here.\n", "Results: EV miR-26a-5p was signifi-\ncantly higher in PsA than in PsV patients (p = 0.004).\n"]

    def test_exact_and_hyphenation(self):
        r = verify("EV miR-26a-5p was significantly higher in PsA than in PsV patients", self.pages, 2)
        self.assertEqual(r["status"], "verified")

    def test_curly_quotes_and_ellipsis(self):
        r = verify("EV miR‑26a‑5p was significantly higher … than in PsV patients", self.pages, 2)
        self.assertEqual(r["status"], "verified")

    def test_wrong_page(self):
        r = verify("EV miR-26a-5p was significantly higher in PsA", self.pages, 1)
        self.assertEqual((r["status"], r["found_page"]), ("wrong_page", 2))

    def test_quote_spanning_pages(self):
        pages = ["... the cohort included patients with psoriatic", "arthritis and plaque psoriasis recruited in 2019."]
        self.assertEqual(verify("patients with psoriatic arthritis and plaque psoriasis", pages, 1)["status"], "verified")

    def test_not_found(self):
        self.assertEqual(verify("TDP-43 was lower in ALS plasma EVs than in controls", self.pages, 2)["status"], "not_found")

    def test_small_typo_tolerated(self):
        self.assertEqual(verify("EV miR-26a-5p was significantly higher in PsA than in PsV patient", self.pages, 2)["status"], "verified")

    def test_unchecked_without_text(self):
        self.assertEqual(verify("anything at all", None, 1)["status"], "unchecked")


class TestConversions(unittest.TestCase):
    def test_sem_to_sd(self):
        m, sd, how = report.to_mean_sd({"stat_type": "mean_sem", "value": 3.8, "spread": 0.3, "n": 25})
        self.assertAlmostEqual(sd, 1.5)
        self.assertIn("SEM", how)

    def test_median_iqr_wan(self):
        m, sd, _ = report.to_mean_sd({"stat_type": "median_iqr", "value": 10, "spread": [8, 14], "n": 50})
        self.assertAlmostEqual(m, 32 / 3)
        from statistics import NormalDist
        self.assertAlmostEqual(sd, 6 / (2 * NormalDist().inv_cdf((0.75 * 50 - 0.125) / 50.25)))
        self.assertAlmostEqual(sd, 4.58, delta=0.01)

    def test_ci(self):
        _, sd, _ = report.to_mean_sd({"stat_type": "mean_ci", "value": 5, "spread": [4, 6], "n": 100})
        self.assertAlmostEqual(sd, 10 * 2 / (2 * 1.984), delta=0.02)

    def test_no_n(self):
        m, sd, how = report.to_mean_sd({"stat_type": "mean_sem", "value": 1, "spread": 0.1, "n": None})
        self.assertIsNone(sd)
        self.assertIn("without n", how)


class TestPlausibility(unittest.TestCase):
    def test_flags(self):
        f = {"finding_type": "null_result", "significant": True,
             "stats": {"groups": [{"label": "a", "n": 10, "value": 100, "spread": 0.5, "stat_type": "mean_sd"},
                                  {"label": "b", "n": 10, "value": 5, "spread": [6, 9], "stat_type": "median_iqr"}],
                       "effect": {"metric": "AUC", "value": 1.3, "ci": [0.6, 0.9]}, "p_value": 0.2},
             "population": {"pct_female": 140, "n_total": 15}}
        flags = set(validate.plausibility(f))
        self.assertTrue({"implausible_spread", "value_outside_spread", "effect_out_of_range", "ci_excludes_estimate",
                         "pct_out_of_range", "n_mismatch", "p_significance_mismatch",
                         "null_result_marked_significant"} <= flags, flags)

    def test_author_key(self):
        self.assertEqual(validate.author_key("Lättekivi F."), "lattekivi f")
        self.assertEqual(validate.author_key("F. Lättekivi"), "lattekivi f")


class TestSchema(unittest.TestCase):
    def test_extraction_schema_catches_errors(self):
        errs = schema.errors({"paper_id": "x", "file_hash": "h", "paper": {"title": "t", "authors": [], "year": 2020},
                              "findings": [{"finding_id": "x-f1", "claim": "short", "finding_type": "bogus"}]},
                             "extraction.schema.json")
        text = "\n".join(errs)
        self.assertIn("authors: needs at least 1", text)
        self.assertIn("bogus", text)
        self.assertIn("missing required field 'evidence'", text)


@unittest.skipIf(pdftext.backend() is None, "no PDF text backend (install pymupdf or poppler)")
class TestPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="litgraph-test-"))
        papers = cls.tmp / "papers"
        papers.mkdir()
        for name, pages in fixture.PAPERS.items():
            fixture.make_pdf(papers / name, pages)
        cls.p = Project(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_full_pipeline(self):
        p = self.p
        self.assertEqual(run_cli(self.tmp, "init", "--topic", "EV miRNAs in PsA")[0], 0)
        code, ing = run_cli(self.tmp, "ingest", "papers")
        self.assertEqual((ing["papers"], ing["to_extract"]), (3, 3))
        corpus = {c["pdf_path"]: c for c in read_json(p.corpus)}
        self.assertEqual(corpus["papers/10.1234_abc.2023.pdf"]["doi"], "10.1234/abc.2023")
        self.assertTrue(corpus["papers/10.1234_abc.2023.pdf"]["id_provisional"])
        self.assertEqual(corpus["papers/pasquali2020.pdf"]["pmid"], "31234567")

        # --- stand-in for the paper-extractor subagents
        todo = read_json(p.work / "todo.json")
        fixture.write_extractions(self.tmp, todo)
        code, chk = run_cli(self.tmp, "check-extraction", str(p.extractions / "pasquali2020.json"))
        self.assertTrue(chk["ok"], chk)
        self.assertEqual(chk["quote_and_plausibility_issues"][0]["status"], "wrong_page")

        code, val = run_cli(self.tmp, "validate")
        self.assertEqual(val["papers"], {"ok": 3})
        self.assertEqual(val["quotes_verified"], "5/6")
        corpus = {c["paper_id"]: c for c in read_json(p.corpus)}
        self.assertIn("chen2023", corpus)  # provisional id finalised
        self.assertEqual(corpus["chen2023"]["author_group"], corpus["lattekivi2022"]["author_group"])
        self.assertNotEqual(corpus["pasquali2020"]["author_group"], corpus["lattekivi2022"]["author_group"])
        findings = {f["finding_id"]: f for f in read_jsonl(p.findings)}
        self.assertIn("chen2023-f2", findings)
        self.assertIn("quote_wrong_page", findings["pasquali2020-f2"]["flags"])
        self.assertTrue(findings["lattekivi2022-f1"]["verified_quote"])

        # --- stand-in for entity resolution
        code, prep = run_cli(self.tmp, "resolve", "prepare")
        self.assertGreaterEqual(prep["entity_names"], 4)
        resolve_input = read_json(p.work / "resolve_input.json")
        self.assertIn(["hsa-miR-192-5p", "miR-192-5p"], [sorted(c) for c in resolve_input["auto_clusters"]["entities"]])
        write_json(p.entities, fixture.ENTITIES)
        write_json(p.cohorts, fixture.cohorts(["lattekivi2022", "chen2023"]))
        code, ap = run_cli(self.tmp, "resolve", "apply")
        self.assertEqual(code, 0, ap)
        self.assertEqual(ap["entity_resolved"], 6)
        self.assertEqual(ap["unresolved_groups"], [])

        # --- candidates + stand-in judge
        code, cand = run_cli(self.tmp, "candidates")
        self.assertGreater(cand["to_judge"], 0)
        self.assertGreater(cand["code_edges"], 0)
        for b in read_json(p.candidates / "index.json")["batches"]:
            write_json(Path(b["out_path"]), fixture.fake_judge(read_json(Path(b["path"]))))
            code, cj = run_cli(self.tmp, "check-judgments", b["out_path"])
            self.assertTrue(cj["ok"], cj)
        code, merged = run_cli(self.tmp, "edges", "merge")
        types = merged["by_type"]
        for t in ("replicates", "contradicts", "shares_cohort", "same_group", "poolable", "method_confound"):
            self.assertIn(t, types, merged)
        edges = read_jsonl(p.edges)
        mc = [e for e in edges if e["type"] == "method_confound"]
        self.assertTrue(all(e["directed"] for e in mc))

        # second round: nothing changed -> everything cached
        code, again = run_cli(self.tmp, "candidates")
        self.assertEqual(again["to_judge"], 0)
        code, merged2 = run_cli(self.tmp, "edges", "merge")
        self.assertEqual(merged2["edges"], merged["edges"])

        # --- report
        code, rep = run_cli(self.tmp, "report")
        md = (p.reports / "connections.md").read_text()
        self.assertIn("miR-26a-5p", md)
        self.assertIn("PsoBank", md)
        self.assertGreaterEqual(rep["poolable_sets"], 1)
        csvs = list(p.reports.glob("poolable_*.csv"))
        self.assertTrue(csvs)
        text = csvs[0].read_text()
        self.assertIn("SEM→SD", text)
        (p.work / "narrative.md").write_text("Two studies agree on miR-26a-5p.")
        run_cli(self.tmp, "report", "--narrative", str(p.work / "narrative.md"))
        run_cli(self.tmp, "report")  # regenerating keeps the narrative
        self.assertIn("Two studies agree", (p.reports / "connections.md").read_text())

        # --- build
        code, b = run_cli(self.tmp, "build")
        html = p.graph.read_text()
        self.assertIn("cytoscape", html)
        self.assertNotIn("__LITGRAPH_DATA__", html)
        data = json.loads(html.split('<script id="litgraph-data" type="application/json">')[1].split("</script>")[0])
        self.assertEqual(data["meta"]["counts"]["findings"], 6)

        self._server_and_reviews(edges)

    def _server_and_reviews(self, edges):
        from litgraph_py import server
        from http.server import ThreadingHTTPServer
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(self.p, "tester"))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            html = urllib.request.urlopen(base + "/").read().decode()
            self.assertIn('"live": true', html)
            self.assertIn('"home": null', html)  # single-project mode has no home page
            pdf = urllib.request.urlopen(base + "/pdf/lattekivi2022").read()
            self.assertTrue(pdf.startswith(b"%PDF"))

            rep = next(e for e in edges if e["type"] == "replicates")

            def post(path, body, origin=None):
                req = urllib.request.Request(base + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
                if origin:
                    req.add_header("Origin", origin)
                return json.loads(urllib.request.urlopen(req).read())

            r = post("/api/review", {"edge_key": rep["edge_key"], "decision": "reject", "note": "different comparator"})
            self.assertEqual(r["state"]["status"], "rejected")
            with self.assertRaises(urllib.error.HTTPError) as cm:
                post("/api/review", {"edge_key": rep["edge_key"], "decision": "accept"}, origin="https://evil.example")
            self.assertEqual(cm.exception.code, 403)
            post("/api/positions", {"positions": {rep["source"]: {"x": 1, "y": 2}}})
            self.assertEqual(read_json(self.p.positions)[rep["source"]], {"x": 1, "y": 2})
            csv = urllib.request.urlopen(base + f"/api/poolable.csv?ids={rep['source']},{rep['target']}").read().decode()
            self.assertIn("study,n1,mean1", csv)
        finally:
            httpd.shutdown()

        # rejected edges drop out of the report...
        report.run(self.p)
        data = read_json(self.p.reports / "report_data.json")
        self.assertTrue(all(rep["edge_id"] not in c["edges"] for c in data["contradiction_clusters"]))
        # ...until a finding changes, which makes the decision stale
        ext_path = self.p.extractions / "lattekivi2022.json"
        ext = read_json(ext_path)
        for f in ext["findings"]:
            f["claim"] += " (re-extracted)"
        write_json(ext_path, ext)
        run_cli(self.tmp, "validate")
        run_cli(self.tmp, "resolve", "apply")
        from litgraph_py.reviews import load_state
        st = load_state(self.p, read_jsonl(self.p.findings))[rep["edge_key"]]
        self.assertEqual(st["status"], "stale")
        code, cand = run_cli(self.tmp, "candidates")
        self.assertGreater(cand["to_judge"], 0)  # changed findings get re-judged


@unittest.skipIf(pdftext.backend() is None, "no PDF text backend (install pymupdf or poppler)")
class TestHub(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="litgraph-hub-"))
        src = self.tmp / "src"
        src.mkdir()
        for name, pages in fixture.PAPERS.items():
            fixture.make_pdf(src / name, pages)
        import zipfile
        self.zip = self.tmp / "My PsA Review.zip"
        with zipfile.ZipFile(self.zip, "w") as zf:
            zf.write(src / "lattekivi2022.pdf", "Review/lattekivi2022.pdf")
            zf.write(src / "pasquali2020.pdf", "Review/sub/pasquali2020.pdf")
            zf.write(src / "pasquali2020.pdf", "__MACOSX/Review/._pasquali2020.pdf")
            zf.writestr("../../escape.pdf", (src / "10.1234_abc.2023.pdf").read_bytes())
            zf.writestr("Review/notes.txt", "not a paper")
            zf.writestr("Review/fake.pdf", b"not really a pdf")
        self.graphs = self.tmp / "graphs"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_new_from_zip(self):
        from litgraph_py import hub
        r = hub.new(str(self.zip), graphs_dir=str(self.graphs))
        self.assertEqual(r["graph"], "my-psa-review")
        papers = sorted(p.name for p in (self.graphs / "my-psa-review" / "papers").iterdir())
        # OS junk, path-traversal entries, non-PDFs and fake PDFs are all skipped; nested folders flattened
        self.assertEqual(papers, ["lattekivi2022.pdf", "pasquali2020.pdf"])
        self.assertFalse((self.tmp / "escape.pdf").exists())
        self.assertFalse(r["existing_graph"])
        again = hub.new(str(self.zip), graphs_dir=str(self.graphs))
        self.assertTrue(again["existing_graph"])
        self.assertEqual((again["added"], again["unchanged"]), (0, 2))

    def test_listing_and_hub_server(self):
        from litgraph_py import hub, server
        from http.server import ThreadingHTTPServer
        r = hub.new(str(self.zip), graphs_dir=str(self.graphs))
        run_cli(r["project"], "ingest", "papers")
        (self.graphs / ".hidden").mkdir()
        graphs = hub.list_graphs(self.graphs)
        self.assertEqual([g["name"] for g in graphs], ["my-psa-review"])
        self.assertEqual((graphs[0]["papers"], graphs[0]["stage"]), (2, "extracting"))

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(server.Site(graphs_dir=self.graphs)))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        get = lambda path: urllib.request.urlopen(base + path)
        try:
            home = get("/").read().decode()
            self.assertIn("my-psa-review", home)
            self.assertNotIn("__LITGRAPH_HOME__", home)
            self.assertEqual(json.loads(get("/api/graphs").read())["graphs"][0]["name"], "my-psa-review")
            self.assertEqual(get("/g/my-psa-review").geturl(), base + "/g/my-psa-review/")  # redirect adds the slash
            self.assertIn("Extracting papers", get("/g/my-psa-review/").read().decode())  # pending page
            self.assertTrue(get("/g/my-psa-review/pdf/lattekivi2022").read().startswith(b"%PDF"))
            for bad in ("/g/../graphs/", "/g/nope/", "/g/%2e%2e/"):
                with self.assertRaises(urllib.error.HTTPError, msg=bad):
                    get(bad)
        finally:
            httpd.shutdown()


if __name__ == "__main__":
    unittest.main()
