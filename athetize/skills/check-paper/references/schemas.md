# Athetize data schemas

All files live in the workspace `<paper>-check/`. `paper.json` is written by `pl.py ingest` and is read-only for Claude.

## paper.json (read only)

```json
{"source_file": "paper.docx", "sha256": "...", "title": "...",
 "paragraphs": [{"id": "p7", "text": "Full paragraph text.", "kind": "body"}]}
```

`kind` is `heading`, `body`, `reference` or `other` (a paragraph with no words, such as a mark in a table). Claims may only point at `body` paragraphs.

A paragraph may also carry its layout, which the report uses to show the paper as it was written: `level` (depth of a heading), `fmt` (bold, italic, subscript and superscript stretches), `tbl` (its table cell), `align` and `figures` (pictures that stand before it). A Word paper with tables has a `tables` list beside `paragraphs`. Leave all of these alone.

## refs.json (Claude writes)

```json
[
  {"id": "1",
   "raw": "Alvarez, M., Chen, L. (2019). Secular trends in adolescent sleep duration. Journal of Sleep Epidemiology.",
   "title": "Secular trends in adolescent sleep duration",
   "authors": ["Alvarez, M.", "Chen, L."],
   "year": 2019,
   "venue": "Journal of Sleep Epidemiology",
   "doi": null,
   "arxiv": null,
   "url": null}
]
```

- `id`: letters, digits, `_`, `.`, `-` only. Use the key that in-text citations use: `1`, `2` for numbered styles, `smith2020` for author-year. Unique.
- Needs at least one of `title`, `raw`, `doi`.
- Copy `doi`, `arxiv` and `url` only when printed in the reference. Never infer or invent them.
- Bibliography items that nothing in the body cites are still listed.

## claims.json (Claude writes, or merged from claims_parts/)

```json
[
  {"id": "c1", "para": "p5",
   "text": "Short sleep is linked to poorer next-day recall of new material [2].",
   "type": "empirical",
   "cites": ["2"],
   "checkworthy": true,
   "note": ""}
]
```

- `para`: id of a body paragraph.
- `text`: an exact substring of that paragraph, including the citation marker. Spans in one paragraph must not overlap. One sentence is the default; use a clause when a sentence holds two independent claims with different citations.
- `type`: `empirical`, `numeric`, `quote`, `background`, `method`, `definition`.
- `cites`: reference ids this claim relies on, taken from the citation marker inside or at the end of the span. `[1, 2]` gives `["1","2"]`. For author-year styles, map each name and year to a ref id. Empty list means uncited.
- `checkworthy`: false for statements that need no check. Prefer leaving them out entirely.
- In `claims_parts/part-NN.json` the `id` field may be omitted or arbitrary; `merge-claims` sorts by position and renumbers `c1..`.

### What counts as a claim

Include: findings, statistics, effect directions, causal statements, definitions attributed to others, descriptions of what a cited study did or found, quotations.
Skip: the paper's own results and methods, hedged opinions, section signposting, rhetorical questions, and common knowledge that no reader would expect a citation for.
Include an uncited sentence only when it states a specific figure or finding a reader would expect to be sourced; leave `cites` empty.
When a citation marker follows a whole paragraph of statements, attach it to the sentences it plausibly covers and say so in `note`.

## notes.json (optional, Claude writes)

```json
[{"id": "n1", "para": "p9", "quote": "We therefore recommend delaying the first bell.",
  "kind": "inconsistency", "severity": "warn",
  "message": "The recommendation rests on a spindle claim that its own source does not support (see c3)."}]
```

`quote` optional, must be an exact substring of the paragraph. `kind`: `inconsistency`, `overreach`, `consistency`. `severity`: `info`, `warn`.

## batches/batch-NNN.json (written by `prepare`)

Each batch lists claims with `claim_id`, `claim_text`, `claim_hash`, `type`, `paragraph_context` and `refs[]`, each ref carrying `ref_id`, `citation`, `evidence_level` (`full_text` or `abstract`), `evidence_hash` and ranked `passages[]` (`rank`, `page`, `text`). `output_file` names where the verifier writes.

## verdicts/batch-NNN.json (claim-verifier writes)

A JSON array, one object per claim and reference pair in the batch:

```json
[{"claim_id": "c1", "ref_id": "2",
  "verdict": "partially_supported",
  "confidence": 0.8,
  "evidence_quote": "Participants recalled 12% fewer words the next morning. The effect held after controlling for baseline memory.",
  "evidence_quotes": ["All 48 participants were healthy adults aged 18 to 30."],
  "analysis": "The study shows the effect after one night of restriction in a lab; the claim generalises to ordinary short sleep."}]
```

`verdict`: `supported`, `partially_supported`, `overstated`, `unsupported`, `contradicted`, `unverifiable`. `evidence_quote` (and each entry of the optional `evidence_quotes`) must be copied character for character from a passage or from the output of `pl.py passages`. Quote whole sentences, enough to cover the claim; the report adds the surrounding source sentences itself. Keep `analysis` to one to three plain sentences.

## suggestions.json (merged by `pl.py merge-suggestions`)

```json
[{"id": "s1", "kind": "rewrite", "claim_id": "c3", "para": "p13",
  "original": "Short sleep is linked to poorer next-day recall of new material [2].",
  "suggested": "In one laboratory study, a night of restricted sleep was linked to poorer next-day recall [2].",
  "refs": ["2"],
  "rationale": "The source tested one night of restriction in a lab; the sentence generalised to ordinary short sleep."}]
```

- `kind`: `rewrite` (a claim's wording), `citation` (an in-text citation marker) or `reference` (a reference-list entry; use the whole paragraph as `original`).
- `original`: exact text that appears once in paragraph `para`. `suggested`: its full replacement; `""` deletes it, and deleting a whole reference paragraph also removes the paragraph.
- `refs`: reference ids the edit concerns. `reference` and `citation` items without `claim_id` show on every claim that cites those refs.
- `rationale`: one or two sentences tied to the evidence or to the lookup that confirms the fix.
- Writers put parts in `suggest_parts/*.json`; `merge-suggestions` assigns ids `s1..` and skips duplicates.

## manifest.json, verdicts.json, report.html

Generated. Do not edit by hand.
