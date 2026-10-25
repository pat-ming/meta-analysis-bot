"""Build a small demo graph in graphs/psa-demo (fake extractions and judgments) to try the viewer:
    python3 tests/make_demo.py && python3 start.py
"""
import sys, json, contextlib, io
from pathlib import Path
root = Path(__file__).resolve().parent.parent
demo = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "graphs" / "psa-demo"
sys.path[:0] = [str(root / "litgraph"), str(root / "tests")]
import fixture
from litgraph_py import cli
from litgraph_py.io import Project, read_json, write_json
def run(*a):
    with contextlib.redirect_stdout(io.StringIO()):
        cli.main(["-C", str(demo), *a])
(demo / "papers").mkdir(parents=True, exist_ok=True)
for n, pg in fixture.PAPERS.items(): fixture.make_pdf(demo / "papers" / n, pg)
p = Project(demo)
run("init", "--topic", "EV miRNAs in PsA (demo)"); run("ingest", "papers")
fixture.write_extractions(demo, read_json(p.work / "todo.json")); run("validate")
write_json(p.entities, fixture.ENTITIES); write_json(p.cohorts, fixture.cohorts(["lattekivi2022", "chen2023"]))
run("resolve", "apply"); run("candidates")
for b in read_json(p.candidates / "index.json")["batches"]:
    write_json(Path(b["out_path"]), fixture.fake_judge(read_json(Path(b["path"]))))
run("edges", "merge"); run("report"); run("build")
print(p.graph)
