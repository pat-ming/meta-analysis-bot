"""litgraph: deterministic half of the litgraph synthesis plugin.

Everything in this package is plain Python (standard library, plus optional
pymupdf). It never calls a language model; the plugin's skills and subagents
do the reading and judging and this package checks, connects and renders.
"""

__version__ = "0.1.0"
PIPELINE_VERSION = __version__
