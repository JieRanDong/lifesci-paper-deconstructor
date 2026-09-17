# lifesci-paper-deconstructor

> A [Claude Code](https://claude.com/claude-code) skill that turns a life-science
> paper PDF into a close-reading report — core conclusions, figure-by-figure and
> panel-by-panel analysis, an appraisal of the paper's "soul" and novelty, and a
> verdict on whether it is top-journal calibre.

Give it a paper PDF. Get back a written report that takes a position on the
paper, backed by specific figures and specific numbers.

**The report is written in Chinese** — the deliverable is a `.docx` typeset for
Chinese readers. The skill itself, and this README, are in English.

Scope: life sciences, including bioinformatics, medicine, neuroscience,
immunology and cancer biology.

---

## Table of contents

- [Why it exists](#why-it-exists)
- [What you get](#what-you-get)
- [The iron rules](#the-iron-rules)
- [How it works](#how-it-works)
- [The two scripts](#the-two-scripts)
- [The appraisal rubric](#the-appraisal-rubric)
- [Installation](#installation)
- [Usage](#usage)
- [Report structure](#report-structure)
- [Known limitations](#known-limitations)
- [Verification](#verification)
- [Licence](#licence)

---

## Why it exists

Anyone can read the abstract and the figure legends. The only value a
close-reading report can add is three things:

1. **Where each figure sits in the argument.** A legend says what experiment was
   run and what was seen. A report has to say which step of the argument that
   observation supports, and what is still missing.
2. **A judgement of evidential strength.** How many independent lines of evidence
   back this conclusion? Correlation or causation? What is *n*? Is there
   orthogonal validation?
3. **The paper's boundary.** What it actually demonstrates, and where it reaches
   past its data.

So the test for any sentence in the report is: **could the reader have gotten
this from the paper directly?** If yes, cut it or compress it. What the reader
*cannot* get directly is the only thing worth writing.

That also defines what this skill refuses to do: translate the abstract, restate
figure legends panel by panel, substitute adjectives ("elegant design",
"substantial dataset") for judgement, or hedge a verdict into "somewhat novel".

---

## What you get

| File | What it is |
|---|---|
| `精读报告.docx` | **The deliverable.** A Chinese-typeset Word document with the original figures embedded, tables, and a proper heading hierarchy. |
| `report.md` | The markdown the `.docx` is built from, so you can revise and rebuild. |
| `work/` | Intermediates — cropped figures, page renders, extracted text, the manifest. Kept, so the run is reproducible. |

Typography is fixed and shared across every report, rather than being whatever
ad-hoc script the moment produced.

---

## The iron rules

These are the core of the skill, and the reason it is not just "ask a model to
summarise this paper".

- **No fabrication.** Every conclusion, sample size and *p*-value must come from
  the paper or its figures. If a panel is unreadable, say *"this panel is not
  legible at the resolution available"* and leave it. Better an empty field than
  a guess.

- **Numbers may come from text, never from eyes.** Every number in the report —
  *n*, *p*, fold-change, percentage, axis value — may be cited **only** when
  written in the body, legend or methods. Do not read values off a graph by eye.
  Judging which bar is taller or which curve is on top is reliable; reading off
  "AUC ≈ 0.15" is not, and a reader has almost no way to catch that error. Use
  qualitative language for trends instead — "markedly lower", "essentially
  absent", "same direction as the control".

  The cost of this rule is low: any number that matters, the authors will have
  written down. And **if a figure's conclusion can only be stated by reading its
  numbers off the plot, say so in the report** — that fact is itself a judgement
  about the figure's evidential strength.

- **Separate what the authors claim from what the data show.** "This suggests X
  may drive Y" in the Discussion, and an actual rescue experiment in Figure 5,
  are two different things. The report must keep them apart.

- **Every judgement carries its evidence.** "Top-journal calibre" must name the
  evidence that cleared the bar; "not good enough" must name the weakest plank.

- **Supplementary figures are usually not in the PDF you were handed — and the
  report must say so.** The body cites `Supplementary Fig. 5` freely, but those
  figures are often not bundled with the main file. **Do not write about what you
  have not seen**, and state the limitation explicitly (the one-minute summary
  has a dedicated line for it). A reader who does not know the supplement is
  missing will take unverifiable paraphrase for established fact — the single
  easiest way for a report like this to mislead.

---

## How it works

| Step | What happens |
|---|---|
| **0 — Confirm input** | The input is a local PDF path. If none was given, ask once; do not guess a filename. If a supplement PDF is provided too, it goes through step 1 as well. |
| **1 — Preprocess the PDF** | Run `pdf_prep.py`: all figure captions in one file, cropped figures at 200 DPI, full-page renders, per-page text, and a data-hygiene note. |
| **2 — Extract the argument** | One-sentence conclusion, 3–5 supporting findings (each tagged with the figure it came from), and the logical spine: problem → strategy → finding → inference. |
| **3 — Figure analysis** | **Main figures panel by panel; supplementary figures figure by figure.** Each main figure ends with a paragraph on what it proves and **what it does not**. |
| **4 — Appraise soul and novelty** | Seven dimensions from `references/top-journal-rubric.md`, with an explicit tier and the evidence for it. |
| **5 — Write, polish, typeset** | Draft `report.md`, work the polish checklist, then build the `.docx`. |

Reading order for step 2: title → abstract → last paragraph of the Introduction
(where the authors state the problem) → every figure legend → first two
paragraphs of the Discussion (where they state the finding) → back to the body to
check the numbers.

For step 3, the panel-level sentence pattern is fixed, and reads like this in the
report:

```
从图1a可以看出，<what this panel did and showed>，这<supports/rules out>了<which link of the argument>。
```

Each panel answers four things: **what was measured** (the design intent — not a
translation of the axis labels, but e.g. "a genome-wide CRISPR screen rather than
candidate validation, so the authors were looking for unknown drivers"), **what
was seen**, **what it contributes to the core claim**, and **how strong that
evidence is** (*n*, model system, whether the statistics fit the design, whether a
missing control should have been there).

---

## The two scripts

Both are self-contained Python scripts run through `uv run --with ...`. Nothing
needs to be installed beforehand.

### `scripts/pdf_prep.py`

Does the mechanical part once, so the reading pass can be about biology instead
of PDF geometry.

```bash
uv run --with pymupdf --with numpy python scripts/pdf_prep.py "<paper.pdf>" --out work/
```

| Argument | Default | Meaning |
|---|---|---|
| `pdf` | — | path to the paper PDF |
| `--out` | required | output directory |
| `--page-dpi` | 130 | full-page render DPI |
| `--fig-dpi` | 200 | cropped figure DPI |
| `--pad` | 4.0 | padding, in points, around each crop |
| `--max-pages` | 0 (all) | stop after N pages |
| `--debug-page` | 0 | print the band structure of one page, for tuning the crop |

Output:

| File | Use |
|---|---|
| `captions.md` | **Read this first.** Every figure caption in one place — the fastest way to see how many figures the paper has and what each claims. |
| `figures/*.png` | Cropped figures, page number in the filename (`fig-001__p02.png`), for panel-level reading. |
| `pages/page-NN.png` | Full-page renders, when you need the layout context. |
| `pages/page-NN.txt` | Per-page body text, for locating where a claim is made. |
| `fulltext.txt` | The whole thing. |
| `manifest.json` | Figure → page mapping, captions, crop boxes. Machine-readable. |
| `notes.md` | **Data-hygiene warnings:** Greek letters lost from the text layer, and how many times the body cites supplementary material. Skim it before writing. |

Figure detection is a **layout heuristic with no per-journal rules**. It has been
checked against Nature Communications, Cell Reports, Genome Biology and bioRxiv
layouts.

> `notes.md` is generated conditionally — if the PDF has no Greek-letter loss and
> the body never cites the supplement, it contains only a title line. That is
> correct behaviour, not a failure.

### `scripts/build_docx.py`

Renders the markdown report into a Chinese-typeset Word document.

```bash
uv run --with python-docx python scripts/build_docx.py "report.md" -o "精读报告.docx"
```

| Argument | Meaning |
|---|---|
| `markdown` | the report markdown file |
| `-o` / `--out` | output `.docx` (defaults to alongside the markdown) |
| `--base-dir` | base directory for resolving relative image paths (defaults to the markdown's directory) |

Supported markdown: `#`–`####` headings (Chinese heading fonts applied), bold /
italic / inline code, unordered lists, tables, images. Image paths are relative
to the markdown file.

**A missing image is not silently dropped** — it leaves a `[缺图: filename]`
marker in the body, and the skill reports which figures did not make it in.

---

## The appraisal rubric

`references/top-journal-rubric.md` defines seven dimensions and a four-tier
verdict scale.

**Dimensions:** A conceptual advance · B completeness of the causal chain ·
C independence and orthogonality of evidence · D methodological or resource
contribution · E distance to translation · F rigour and reproducibility ·
G timing and competitive landscape

**Tiers:**

| Verdict | Characteristics |
|---|---|
| Top journal (CNS) | Dimension A proposes a field-level new concept; B and C have no weak spot |
| Field-leading (Nature sub-journals, Cell Reports, PNAS, Genome Biology, …) | B and C solid and complete, A moderate — where most solid work lands |
| Solid but not enough | A clear gap in B or C: no rescue, no in vivo, a single line of evidence |
| Incremental | Low A plus a break in B; the conclusion is predictable |

Appraisals must attach to **specific figures and specific data**. *"The evidence
chain breaks at Figure 4: the knockdown phenotype is clean in vitro, but the
in vivo experiment in Figure 4 is n=3 with no rescue arm"* is a passing
judgement. *"The evidence is not yet sufficient"* is not.

---

## Installation

```bash
git clone https://github.com/JieRanDong/lifesci-paper-deconstructor.git \
  ~/.claude/skills/lifesci-paper-deconstructor
```

Claude Code discovers skills in `~/.claude/skills/`, so cloning there is the
whole install.

### Requirements

| Requirement | Notes |
|---|---|
| [Claude Code](https://claude.com/claude-code) | the host |
| [`uv`](https://docs.astral.sh/uv/) on `PATH` | both scripts pull their dependencies via `uv run --with` |
| `pymupdf`, `numpy` | used by `pdf_prep.py` only; fetched automatically |
| `python-docx` | used by `build_docx.py` only; fetched automatically |

No API keys, no cost. If you hit a GitHub connection error while cloning, this
repository was pushed over SSH because `github.com:443` is intermittently
unreachable from some networks — see [Known limitations](#known-limitations).

---

## Usage

The skill triggers from natural language; you do not invoke it by name. It fires
on: *close-read this paper*, *break down this paper*, *journal club*, *group
meeting report*, *explain each figure*, *what does this figure show*, *is this
novel enough for a top journal*, *what are this paper's strengths and weaknesses*
— or simply handing over a PDF path.

Example prompts:

```
Close-read this paper for me: D:/papers/pnas.202020478.pdf
Walk me through every figure and tell me whether this is Nature-subjournal quality.
I have to present this at group meeting — write me the report.
I've got the supplementary PDF too, read that as well.
```

If a supplementary PDF is supplied, main figures are analysed panel by panel and
supplementary figures figure by figure.

---

## Report structure

The markdown and the `.docx` share a fixed structure; the content adapts to the
paper.

```
〇  Background            field & core question → prior understanding → the gap → this paper's angle
一  One-minute summary    one-sentence conclusion / paper type / supplement attached? / soul rating / evidence rating
二  Core argument         the one sentence + supporting findings (tagged with figures) + the spine and its weakest link
三  Figure analysis       main figures panel by panel (incl. "what it proves, what it doesn't") / supplements figure by figure
四  Soul & novelty        the soul / seven-dimension table / why it is or is not top-journal / the weakest plank
五  Three sentences       what to remember / where to cite it / what to read next
```

Two sections carry extra requirements. **Background** must install a coordinate
system — what problem the paper solves and why it is worth solving — in four
moves (field and core question, prior understanding, the knowledge gap, this
paper's angle), with three prohibitions (don't copy the Introduction's first
paragraph, don't write a field review, don't dodge why the field was stuck).
**The polish checklist** has eight items, of which item 5 is the load-bearing
one: every number in the report must trace back to a sentence in the source;
anything that cannot is a number read off a plot by eye and must be deleted or
turned into a qualitative statement.

---

## Known limitations

- **Supplementary material must be supplied separately.** The `Supplementary
  Fig. X` the body cites is usually not in the main PDF. The skill states this
  limitation in the report rather than paraphrasing figures it never saw.
- **Figure detection is a layout heuristic.** Verified on four common layouts,
  but it can fail on mixed single/double-column pages, unusual designs, or
  figures that break across pages. If `manifest.json` contains `no image
  extracted`, use the full-page render `pages/page-NN.png` instead and say so in
  the report.
- **A text layer is required.** Scanned PDFs have no extractable body text or
  captions; OCR them first.
- **This is not a literature search tool.** It works on PDFs you already have.
- **The report is in Chinese.** Terms get their English original on first use,
  but the output language is Chinese.
- **It gives judgements, and judgements can be wrong.** The soul rating,
  evidence rating and tier verdict are assessments of the paper, not facts. Each
  one carries its stated evidence precisely so you can check it yourself.

---

## Verification

Both scripts were exercised before publishing (2026-09-17):

- **`pdf_prep.py` on a 9-page PNAS paper:** found **4 figures**, each with a label,
  page number and full caption, across 26 output files. `captions.md` organised
  them as `## Fig. 1 (figure on page 2, caption on page 2)`; `manifest.json`
  carried `figures[].label / page / caption_page / caption`.
- **`notes.md` conditional generation confirmed:** that paper had no Greek-letter
  loss and cited no supplementary material, so the file contained only its title
  line — as designed.
- **`build_docx.py` on a seven-section Chinese fixture:** produced a
  53-paragraph document with an 8 × 3 table, heading levels H1 ×1 / H2 ×6 /
  H3 ×9 / H4 ×2, and SimSun applied as the East Asian font.
- **Image handling confirmed in both directions:** a real cropped figure was
  embedded; a reference pointing at a nonexistent file was **not** embedded and
  instead left a `[缺图: does-not-exist.png]` marker in the body.

---

## Licence

No licence file is included. Absent one, the default is all rights reserved —
add one if you intend others to reuse this.
