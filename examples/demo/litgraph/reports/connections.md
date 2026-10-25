# Connections: EV miRNAs in PsA (demo)

*3 papers · 6 findings · 13 connections (0 accepted by a reviewer) · generated 2026-10-24T21:53 · litgraph 0.1.0*

<!-- litgraph:narrative:start -->
*Narrative summary not written yet.*
<!-- litgraph:narrative:end -->

## Replication by entity

| Entity | Type | Papers | Findings | Replicates | Conflicts | Groups | Verdict |
|---|---|---:|---:|---:|---:|---:|---|
| miR-26a-5p | miRNA | 3 | 3 | 1 | 2 | 2 | mixed |
| miR-192-5p | miRNA | 2 | 2 | 0 | 1 | 2 | conflicting |

1 further entities were reported by a single paper only.

**Overlap across papers**

- 2 of 3 miRNA entities were reported by two or more papers

## Contradictions

### 1. miR-26a-5p (3 findings)

- **Chen 2023** (`chen2023-f2`): miR-26a-5p does not differ between PsA and PsV.
- **Lattekivi 2022** (`lattekivi2022-f1`): EV miR-26a-5p is higher in PsA than in PsV.
- **Pasquali 2020** (`pasquali2020-f1`): Plasma EV miR-26a-5p is higher in PsA than in cutaneous psoriasis.

- *contradicts* `chen2023-f2` ↔ `lattekivi2022-f1`: Rule-based test judgment for this pair.
- *contradicts* `chen2023-f2` ↔ `pasquali2020-f1`: Rule-based test judgment for this pair.

Candidate explanations:
- Rule-based test judgment for this pair. (`chen2023-f2` → `pasquali2020-f1`, confidence 0.4)

### 2. miR-192-5p (2 findings)

- **Lattekivi 2022** (`lattekivi2022-f2`): Serum EV miR-192-5p is higher in PsA than in PsV.
- **Pasquali 2020** (`pasquali2020-f2`): miR-192-5p is lower in PsA than in PsC.

- *contradicts* `lattekivi2022-f2` ↔ `pasquali2020-f2`: Rule-based test judgment for this pair.

Candidate explanations:
- Rule-based test judgment for this pair. (`lattekivi2022-f2` → `pasquali2020-f2`, confidence 0.4)

## Independence warnings

**Shared cohorts**

- PsoBank: Lattekivi 2022, Chen 2023

**Papers from the same research group**

- `group-koks`: Chen 2023, Lattekivi 2022

## Poolable sets

- **miR-26a-5p, Psoriatic arthritis vs Psoriasis without arthritis**: 2 studies; statistics reported as mean_sd, mean_sem; 2 rows complete after conversion → `litgraph/reports/poolable_mir-26a-5p_psoriatic-arthritis-vs-psoriasis-without.csv`

## Data quality

- 1 findings have a quote that could not be verified on the stated page
- 1 findings carry validation flags
  - `quote_wrong_page`: 1
