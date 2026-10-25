# litgraph

litgraph is a Claude Code plugin for systematic reviews. You give it a zip of papers. It reads them,
extracts every finding with a verbatim quote and page number, and works out how the findings
relate:

- which findings **replicate**
- which **contradict** each other
- which **aren't independent** (same cohort or same research group)
- which **method differences** might explain a disagreement
- which studies can be **pooled** for meta-analysis

You then explore the results as an interactive graph in your browser and accept or reject each
connection.

- **Claude Code** does the reading and judging, on your Claude subscription. No API key or credits
  are needed.
- **Plain Python** does everything else: checking quotes against the PDFs, building the graph,
  serving the viewer. It needs no installs.

---

## Requirements

| What | Why | Check |
|---|---|---|
| [Claude Code](https://code.claude.com) | Runs the plugin | `claude --version` |
| A paid Claude plan (Pro, Max, Team or Enterprise) | Papers are read in parallel with *workflows*. On Pro, turn them on in `/config`. Without workflows the plugin falls back to ordinary subagents, which is slower but works. | |
| Python 3.9 or newer | The CLI, the viewer and `start.py` | `python3 --version` |
| **Recommended:** `pymupdf` *or* poppler | Reads the PDF text so every quote can be checked against its page. Without either, quotes are marked "unchecked". | see below |

To install a PDF text reader, pick one:

```bash
pip install pymupdf          # any OS
brew install poppler         # macOS (provides pdftotext)
sudo apt install poppler-utils   # Debian/Ubuntu
```

On Windows, use `python` wherever this guide says `python3`.

---

## Setup on a new computer

1. **Get the code.** Copy this whole folder to the computer (zip it up, or `git clone` it if you've
   put it in a repository). Everything else below runs from inside it:

   ```bash
   cd meta-analysis-bot
   ```

   It should contain:

   ```
   meta-analysis-bot/
   ├── start.py          ← starts the viewer
   ├── litgraph/         ← the Claude Code plugin
   ├── graphs/           ← your reviews (created automatically)
   └── tests/
   ```

2. **Check it works** (optional, takes a few seconds):

   ```bash
   python3 -m unittest discover -s tests
   ```

   Some tests are skipped if no PDF text reader is installed. That's fine.

---

## Running a review end to end

### 1. Start Claude Code with the plugin

From the `meta-analysis-bot` folder:

```bash
claude --plugin-dir ./litgraph
```

You'll know it loaded when typing `/litgraph` offers `/litgraph:synthesize`.

### 2. Give it your papers

Put the PDFs for one review in a zip, then in Claude Code:

```
/litgraph:synthesize ~/Downloads/ALS-EV.zip --topic "Extracellular vesicle biomarkers in ALS"
```

- The graph is named after the zip (`ALS-EV.zip` becomes `graphs/als-ev/`). Use `--name` to choose
  another name.
- A folder of PDFs works in place of a zip.
- Nested folders in the zip are fine. macOS junk files and anything that isn't a PDF are skipped.

Claude then works through these stages, reporting progress as it goes:

1. **Ingest:** hashes each PDF and finds DOIs and PMIDs.
2. **Extract:** one agent per paper pulls out findings, statistics, methods, cohorts and quotes.
3. **Validate:** code checks every quote against the PDF page and flags implausible numbers (for
   example an SEM reported as an SD).
4. **Resolve:** merges synonyms (UMOD = uromodulin), keeps distinct entities apart (miR-199a-3p vs
   -5p), and identifies shared cohorts.
5. **Connect:** pairs findings that could be related, and agents classify each pair.
6. **Report:** writes `connections.md` and pooling-ready CSVs.
7. **Build:** writes the graph.

A 40-paper review takes a while and uses a fair amount of your plan's usage. You may be asked to
approve the workflow and some commands the first time.

### 3. Open the viewer

In a **separate terminal**, from the `meta-analysis-bot` folder:

```bash
python3 start.py
```

Your browser opens at **http://127.0.0.1:8765/**. The home page lists every review in `graphs/`
with its paper, finding and connection counts and how much you've reviewed. It updates on its own,
so you can leave it open while Claude is still working. Click a card to open its graph. Press
Ctrl+C in the terminal to stop the viewer.

Options: `python3 start.py --port 9000`, `--no-open` (don't open a browser), `--reviewer "Your Name"`
(the name saved on your decisions; defaults to your computer login).

The viewer only listens on your own machine (`127.0.0.1`), so nobody else on the network can reach it.

### 4. Review the graph

- **Nodes are findings.**
  - Colour shows the paper, entity or direction (your choice).
  - Shape shows the type of finding.
  - Size shows the sample size.
  - A hollow node is a null result. ⚠ means it was flagged in validation. A dotted outline means
    its quote couldn't be found on the stated page.
- **Edges are connections.** Green supports, red conflicts, amber is a caution, grey is neutral.
  Faint edges haven't been reviewed yet.
- **Click a finding** to see its numbers, methods, quotes, and buttons that open the PDF at the
  right page or go to the DOI or PubMed record.
- **Click a connection** to see the rationale and both quotes side by side, then **Accept**,
  **Reject**, **Change type** or **Add note**. Decisions save immediately.
- **Double-click a finding** to show only it and its neighbours. Press Esc to exit.
- **Views** on the left: *Replication*, *Independence* and *Poolable* (with a CSV export for each
  set).
- **Panels:** drag the edges of the side panels to resize them, or double-click an edge to hide or
  show that panel.
- **Theme:** the ◐ button switches between system, light and dark.
- **Layout:** drag nodes to arrange them; the layout is remembered.
- **← Graphs** takes you back to the home page.

---

## Adding papers later

Run the same command with a zip of the same name (or pass `--name`):

```
/litgraph:synthesize ~/Downloads/ALS-EV.zip
```

New papers are added to the existing graph. Papers already read are taken from the cache, and pairs
already judged aren't judged again, so only the new work uses your plan. Your accept/reject
decisions stay attached. If a finding changes because its PDF changed, the decisions on its
connections are marked **stale** so you can look at them again.

Each review lives entirely in its own folder, so separate reviews never affect each other.

---

## What's in a graph folder

```
graphs/als-ev/
├── papers/                      your PDFs
└── litgraph/
    ├── corpus.json              one entry per paper
    ├── extractions/             raw extraction per paper (the cache)
    ├── findings.jsonl           the nodes
    ├── entities.json            canonical entities, aliases, comparison groups
    ├── cohorts.json             cohorts and which papers use them
    ├── edges.jsonl              the connections
    ├── reviews.jsonl            your decisions (written by the viewer)
    ├── positions.json           your node layout (written by the viewer)
    ├── reports/
    │   ├── connections.md       narrative summary and tables
    │   └── poolable_*.csv       ready for R/metafor
    └── graph.html               standalone read-only copy of the graph
```

Everything is plain JSON, Markdown and CSV, so it can be versioned and inspected. Never delete
`reviews.jsonl` or `positions.json`: they hold your own work. Everything else can be regenerated.

**Sharing:**
- To share a read-only view, send `graph.html` alone; it opens in any browser with no internet
  needed.
- To share everything, including the ability to review, copy the whole `graphs/<name>/` folder into
  the other person's `graphs/`.

---

## Command reference

Inside Claude Code, while the plugin is enabled, the `litgraph` command is available to Claude.
Outside Claude Code, run it by its full path: `./litgraph/bin/litgraph`.

| Command | Does |
|---|---|
| `litgraph new <zip-or-folder> [--name N]` | Create or extend `graphs/<name>/` from PDFs |
| `litgraph -C graphs/<name> status` | Show where that graph's pipeline is |
| `litgraph -C graphs/<name> build` | Rebuild the standalone `graph.html` |
| `litgraph -C graphs/<name> report` | Regenerate `connections.md` and the CSVs |
| `litgraph -C graphs/<name> view` | Serve just that one graph |
| `litgraph serve` | Same as `python3 start.py` |

Run `./litgraph/bin/litgraph --help` for the rest (the individual pipeline stages).

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `/litgraph:synthesize` doesn't appear | Start Claude Code from this folder with `claude --plugin-dir ./litgraph`. Run `/reload-plugins` after editing plugin files. |
| Quotes all say "unchecked" | No PDF text reader installed. Install `pymupdf` or poppler (see Requirements) and run the command again. |
| A scanned PDF has no findings or no verified quotes | It has no text layer. Run it through OCR (e.g. `ocrmypdf`) and add it again. |
| Port 8765 is busy | `start.py` tries the next free port automatically and prints the address it chose. |
| Workflows unavailable | The plugin falls back to ordinary subagents, which is slower. On Pro, enable workflows in `/config`. |
| `litgraph: command not found` in your own terminal | Expected: it's only on PATH inside Claude Code. Use `./litgraph/bin/litgraph`. |
| The home page says a graph is stuck "Extracting" | The run was interrupted. Run `/litgraph:synthesize` with the same zip again; finished papers are skipped. |

---

## Try it without real papers

```bash
python3 tests/make_demo.py   # builds graphs/psa-demo from 3 synthetic papers
python3 start.py
```

The demo's findings and connections were written by a script, not by Claude, so they only show how
the viewer works.
