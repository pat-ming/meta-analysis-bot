---
name: synthesize
description: Build a litgraph findings graph from a folder of papers for a systematic review. Reads every PDF, extracts findings with verified quotes, resolves entities and cohorts, judges how findings connect (replicates, contradicts, shares cohort, method confound, poolable), and writes connections.md, poolable CSVs and an interactive graph.html. Use when the user runs /litgraph:synthesize or asks to synthesize, map or connect the findings in a set of papers.
argument-hint: "[papers-folder] [--topic \"review topic\"]"
allowed-tools: Bash(litgraph:*), Bash(${CLAUDE_PLUGIN_ROOT}/bin/litgraph:*), Read, Write, Edit, Agent, Workflow
---

# litgraph: synthesize

Turn a folder of papers into a graph of findings and connections. You run the pipeline; the
`litgraph` CLI does everything deterministic (hashing, schema checks, quote verification, pair
blocking, merging, reports, rendering) and prints a JSON summary after every command. You and the
subagents only read and judge.

Arguments: `$ARGUMENTS` (papers folder, default `papers/`; optional `--topic "..."`).

- Run every command from the user's review project folder (the folder that holds the papers folder).
  All outputs go to `./litgraph/` there.
- `litgraph` is on PATH while this plugin is enabled. If the shell can't find it, use
  `${CLAUDE_PLUGIN_ROOT}/bin/litgraph` instead.
- Plugin root, for paths you pass to workflows: `${CLAUDE_PLUGIN_ROOT}`
- Read each command's JSON output before going on. Don't print whole project files into the
  conversation; read the parts you need.
- Re-running is cheap: extractions are cached by PDF hash and judgments by finding content, so only
  new or changed papers and pairs are processed. Never delete `litgraph/reviews.jsonl` or
  `litgraph/positions.json`; they hold the human's work.

Keep the user posted with one short line per stage (e.g. "Extracting 12 papers…", "Resolved 214
entity names into 131 entities").

## 1. Set up and ingest

```bash
litgraph init --topic "<topic>"          # topic: from --topic, else infer a short one from the folder or ask
litgraph ingest <papers-folder>
```

`ingest` reports `papers`, `to_extract`, `cached`, `changed`, and `text_backend`. If
`text_backend` is null, quotes can't be verified: tell the user to `pip install pymupdf` (or
`brew install poppler`) and offer to continue anyway. If `papers` is 0, stop and say so.

## 2. Extract (one paper-extractor per paper)

If `to_extract` > 0, read `litgraph/work/todo.json` and run the bundled workflow:

- Workflow name `litgraph:extract-all`, args
  `{"project": "<absolute project path>", "plugin_root": "${CLAUDE_PLUGIN_ROOT}", "papers": <the "papers" array from todo.json>}`

If the Workflow tool isn't available, dispatch the `litgraph:paper-extractor` subagent yourself
with the Agent tool, up to 8 at a time in parallel, giving each the paper's fields from todo.json
plus the project folder and the schema path `${CLAUDE_PLUGIN_ROOT}/schemas/extraction.schema.json`.

## 3. Validate

```bash
litgraph validate
```

This checks each extraction against the schema, verifies every quote against the PDF text on the
stated page, flags implausible numbers, finalises paper IDs, works out research groups from author
overlap, and writes `litgraph/findings.jsonl`.

Look at `papers` in the summary. For any paper with status `invalid` or `missing`, or with many
quote failures in `litgraph/work/validation_report.json`, re-dispatch the paper-extractor for those
papers only, telling it what failed. Do one repair round, then move on and tell the user which papers
still have problems. Don't edit extractions by hand except for trivial schema fixes.

## 4. Resolve entities, comparisons and cohorts (you do this yourself)

```bash
litgraph resolve prepare
```

Read `litgraph/work/resolve_input.json`. It lists every entity name, every case/control group
label, and every cohort mention, with counts, papers, example claims, automatic near-duplicate
clusters, and any existing resolution (keep existing IDs stable).

Write `litgraph/entities.json`:

```json
{
  "entities": [
    {"entity_id": "mir-26a-5p", "canonical": "miR-26a-5p", "type": "miRNA",
     "aliases": ["miRNA-26a-5p", "hsa-miR-26a-5p"],
     "related": [{"entity_id": "mir-26a-3p", "relation": "same_precursor_other_arm"}]}
  ],
  "groups": [
    {"group_id": "psa", "canonical": "Psoriatic arthritis (PsA)", "aliases": ["PsA", "psoriatic arthritis"], "parent": null},
    {"group_id": "psv", "canonical": "Plaque psoriasis (PsV)", "aliases": ["PsV", "psoriasis vulgaris"], "parent": "non-psa-psoriasis"},
    {"group_id": "non-psa-psoriasis", "canonical": "Psoriasis without arthritis", "aliases": ["PsC", "cutaneous psoriasis"], "parent": null}
  ]
}
```

and `litgraph/cohorts.json`:

```json
{"cohorts": [{"cohort_id": "sprint", "name": "SPRINT", "aliases": ["Systolic Blood Pressure Intervention Trial"],
  "registry_id": "NCT01206062", "papers": ["ikeme2024", "ikeme2025"],
  "evidence": [{"paper_id": "ikeme2024", "quote": "...", "page": 2}]}]}
```

Entity resolution is the crux of the whole tool. Rules:

- **Merge true synonyms only** (uromodulin = UMOD = Tamm–Horsfall protein; hsa-miR-21-5p = miR-21-5p).
- **Link, don't merge, near-misses that are biologically distinct**: miRNA arms (-3p/-5p), isoforms,
  phosphorylated vs total protein, receptor vs ligand (RANK / RANKL), EV subpopulations. Use
  `related` with a short `relation` (`same_precursor_other_arm`, `ligand_receptor`, `isoform`,
  `phospho_form`, `same_pathway`, …). Linked entities get compared too.
- A bare `miR-26a` where the paper doesn't give the arm: keep it as its own entity and relate it to
  both arms, unless the paper's methods make the arm clear.
- **Groups** are resolved the same way, with `parent` for hierarchy (PsV ⊂ psoriasis without
  arthritis). Healthy controls, disease controls and mimics are different groups.
- **Cohorts**: one entry per real cohort; list every paper that uses it and a quote from each. Only
  merge cohort mentions that are clearly the same participants (same name, registry ID, or the same
  recruitment site and period).
- IDs: lowercase kebab-case, stable across runs.

Then:

```bash
litgraph resolve apply
```

Fix every item in `problems`, and add any `unresolved_entities` / `unresolved_groups` to the files,
then re-run until both are empty.

## 5. Connect (one edge-judge per batch)

```bash
litgraph candidates
```

Code pairs findings across papers that share an entity, a related entity or an identical baseline
table, and writes judge batches; caution edges for shared cohorts and research groups are made by
code directly. (Add `--include-comparison` to also pair different entities measured in the same
comparison; this costs many more pairs, so only do it if the user asks.)

If `to_judge` > 0, read `litgraph/work/candidates/index.json` and run the workflow
`litgraph:judge-all` with args
`{"project": "<absolute project path>", "plugin_root": "${CLAUDE_PLUGIN_ROOT}", "batches": <the "batches" array>}`.
Without the Workflow tool, dispatch `litgraph:edge-judge` subagents yourself (up to 8 in parallel),
one per batch, with the batch path, out_path, project folder and the schema path
`${CLAUDE_PLUGIN_ROOT}/schemas/judgments.schema.json`.

Then:

```bash
litgraph edges merge
```

If `batches_without_output` is not empty, re-run the judge for those batches once, then merge again.

## 6. Report

```bash
litgraph report
```

Read `litgraph/reports/connections.md` and `litgraph/reports/report_data.json`. Write a narrative
summary of 300–600 words in the voice of the review's results section: which findings replicate
across independent groups, the overlap rates ("4 of 95 miRNAs were reported by more than one
study"), the main contradiction clusters and their most likely methodological explanations, the
independence problems (shared cohorts, single-group findings), and which sets are ready to pool.
Cite papers as "Author Year" and only state what the tables and edges support. Save it to
`litgraph/work/narrative.md`, then:

```bash
litgraph report --narrative litgraph/work/narrative.md
```

## 7. Build and hand off

```bash
litgraph build
```

Finish with a short summary for the user: papers, findings, quote-verification rate, edges by
polarity, the two or three most important connections, and any papers that still have problems.
Then tell them how to explore and review the graph:

- `open litgraph/graph.html` for a read-only view, or
- `litgraph view` in a terminal to review edges and save decisions. Outside Claude Code the
  command isn't on PATH; give them the full path: `${CLAUDE_PLUGIN_ROOT}/bin/litgraph view`
  (run from the project folder).

Don't start `litgraph view` yourself: it runs a server until stopped.
