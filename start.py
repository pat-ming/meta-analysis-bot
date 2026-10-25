#!/usr/bin/env python3
"""Start litgraph: a local server with a home page listing every graph in
graphs/, and the interactive viewer for each one.

    python3 start.py                 # opens http://127.0.0.1:8765/
    python3 start.py --port 9000 --no-open
"""

import argparse
import sys
from pathlib import Path

if sys.version_info < (3, 9):
    sys.exit("litgraph needs Python 3.9 or newer")

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "litgraph"))

from litgraph_py import server  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graphs", default=str(ROOT / "graphs"), help="graphs folder (default: ./graphs next to this file)")
    ap.add_argument("--port", type=int, default=8765, help="port to try first (default 8765)")
    ap.add_argument("--no-open", action="store_true", help="don't open a browser")
    ap.add_argument("--reviewer", help="name recorded on review decisions (default: your login)")
    a = ap.parse_args()
    server.serve_hub(Path(a.graphs).expanduser().resolve(), a.port, not a.no_open, a.reviewer)


if __name__ == "__main__":
    main()
