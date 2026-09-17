#!/usr/bin/env python3
"""Prepare a life-science paper PDF for figure-by-figure reading.

Why this exists: a model reading a PDF page can see the figures, but it cannot
reliably tell *which* figure sits on *which* page, or hand a reader the figure
itself. This script does the mechanical part once, so the reading pass can be
about biology instead of PDF geometry.

How figures are located (no per-journal rules; checked against Nature
Communications, Cell Reports, Genome Biology and bioRxiv layouts):

  1. Each page is rendered to a coarse grayscale bitmap and split into
     horizontal bands at ink-free scan rows. Every band gets its vertical
     extent and its horizontal ink extent.
  2. A band that overlaps a vector drawing or an embedded raster image is
     graphic. Body text has neither, which is what separates a figure from the
     paragraphs next to it. A text-only band spanning most of the column width
     is a line of body text; a narrow one is a panel letter or axis label.
  3. Contiguous graphic bands form a *figure region*. Narrow text bands between
     them are bridged, so multi-row figures stay whole, while full-width text
     breaks the run.
  4. Captions are matched to the nearest figure region on the same page. Many
     publishers (Nature family especially) float a figure to the bottom of one
     page and its caption to the top of the next, so unclaimed captions are
     paired with an unclaimed region on an adjacent page.
  5. The crop box is read back off the rendered pixels, so it hugs the actual
     ink whether the figure is vector or bitmap.

Outputs (into --out):
  pages/page-NN.txt     per-page plain text (locates any claim's source sentence)
  pages/page-NN.png     full-page render, for reading text+layout in context
  figures/fig-XX_*.png  cropped figure at high DPI, for panel-level detail
  captions.md           every figure caption, with its page and crop status
  manifest.json         machine-readable map: figure -> page, caption, crop box
  fulltext.txt          whole document as one text file

Usage:
  uv run --with pymupdf python pdf_prep.py paper.pdf --out work/
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
import pymupdf

# --------------------------------------------------------------------- captions
# A caption starts a text block with its label. In-text citations ("...as shown
# in Fig. 1b") almost never start a block, and when they do they are followed by
# a panel letter or a closing bracket - which the lowercase guard rejects.
CAP_RE = re.compile(
    r"^((?:Extended\s+Data\s+|Supplementary\s+|Suppl\.?\s+|Extended\s+)?"
    r"(?:Fig(?:ure)?s?|Table)\.?\s*[S]?\d+)"
    r"\s*[|.:—–\-]?\s*(.*)$",
    re.IGNORECASE | re.DOTALL,
)

MIN_CAPTION_CHARS = 60    # a caption is a sentence, not a cross-reference
INK_LEVEL = 245           # 0-255; below this a pixel counts as ink
INK_ROW_FRAC = 0.004      # fraction of a row that must be inked to be non-blank
WIDE_TEXT_FRAC = 0.80     # body text spans the text block; figure labels are inset
PARA_HEIGHT_PT = 25.0     # a body-text paragraph is a multi-line band this tall
GROUP_GAP_PT = 45.0       # graphic bands closer than this belong to one figure
MIN_REGION_PT = 90.0      # a real figure is at least this tall
MAX_PAIR_GAP_PT = 150.0   # how far a caption may sit from its figure
EDGE_EXPAND_PT = 18.0     # absorb a panel letter just outside the graphic ink


def _norm(label: str) -> tuple[str, str]:
    """'Extended Data Fig. 1a' -> ('fig', '001');  'Table 2' -> ('table', '002')."""
    m = re.search(r"(Fig(?:ure)?|Table)\.?\s*([S]?\d+)", label, re.IGNORECASE)
    if not m:
        return ("other", label.strip().lower())
    kind = "table" if m.group(1).lower().startswith("table") else "fig"
    raw = m.group(2).upper()
    return (kind, f"{int(raw.lstrip('S')):03d}" + ("s" if raw.startswith("S") else ""))


def _is_caption(body: str) -> str | None:
    """Return the figure label if this text block is a caption, else None."""
    if len(body) < MIN_CAPTION_CHARS:
        return None
    m = CAP_RE.match(body)
    if not m:
        return None
    rest = m.group(2).strip()
    if not rest or rest[0].islower() or rest[0] in "),;":
        return None   # "...Fig. 5c, d). Remarkably..." is body text
    return " ".join(m.group(1).split())


def _graphic_rects(page: pymupdf.Page) -> list[pymupdf.Rect]:
    """Bounding boxes of vector graphics and embedded raster images.

    cluster_drawings() is PyMuPDF's native grouping of vector primitives. It
    matters here: a single figure panel can be thousands of strokes, and
    clustering them ourselves would dominate the runtime.
    """
    rects: list[pymupdf.Rect] = []
    try:
        rects.extend(page.cluster_drawings())
    except Exception:
        pass
    for info in page.get_image_info():
        r = pymupdf.Rect(info["bbox"])
        if r.is_valid and not r.is_empty:
            rects.append(r)
    # Drop hairlines and rules: table borders, underlines, page furniture.
    return [r for r in rects if (r.width > 8 or r.height > 8) and (r.width * r.height) > 150]


def _bands(page: pymupdf.Page, dpi: int = 72):
    """Split the page into ink bands, each with its horizontal extent."""
    rect = page.rect
    pm = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    W, H = pm.width, pm.height
    ys, xs = rect.height / H, rect.width / W
    ink = np.frombuffer(pm.samples, dtype=np.uint8).reshape(H, W) < INK_LEVEL

    inked = ink.mean(axis=1) > INK_ROW_FRAC
    bands = []
    start = None
    for y, on in enumerate(list(inked) + [False]):
        if on and start is None:
            start = y
        elif not on and start is not None:
            cols = np.flatnonzero(ink[start:y].any(axis=0))
            bands.append({"y0": start * ys, "y1": y * ys,
                          "x0": (cols[0] if cols.size else 0) * xs,
                          "x1": (cols[-1] if cols.size else 0) * xs,
                          "graphic": False})
            start = None

    rects = sorted(_graphic_rects(page), key=lambda r: r.y0)
    for b in bands:
        for r in rects:
            if r.y0 > b["y1"]:
                break
            if r.y1 > b["y0"]:
                b["graphic"] = True
                break
    return bands, pm, ys, xs


def _figure_regions(bands, page_rect) -> list[dict]:
    """Group graphic bands into candidate figure regions."""
    def wide(b):
        return (b["x1"] - b["x0"]) > WIDE_TEXT_FRAC * page_rect.width

    gidx = [i for i, b in enumerate(bands) if b["graphic"]]
    if not gidx:
        return []

    groups: list[list[int]] = [[gidx[0]]]
    for i in gidx[1:]:
        prev = groups[-1][-1]
        gap = bands[i]["y0"] - bands[prev]["y1"]
        # Text between two graphic bands may be inside the figure (panel
        # letters, axis labels) or may be body text separating two figures.
        # Body text shows up as a paragraph-tall band or as a run of stacked
        # full-width lines; a single inset label row does not.
        between = bands[prev + 1:i]
        wide_run = [b for b in between if wide(b)]
        is_body_text = (any(b["y1"] - b["y0"] >= PARA_HEIGHT_PT for b in wide_run)
                        or len(wide_run) >= 2)
        if gap < GROUP_GAP_PT and not is_body_text:
            groups[-1].append(i)
        else:
            groups.append([i])

    regions = []
    for g in groups:
        y0, y1 = bands[g[0]]["y0"], bands[g[-1]]["y1"]
        # Absorb an immediately adjacent narrow band: the panel letter often
        # sits a few points above the topmost plot.
        for j in (g[0] - 1, g[-1] + 1):
            if not (0 <= j < len(bands)):
                continue
            b = bands[j]
            gap = y0 - b["y1"] if j < g[0] else b["y0"] - y1
            if 0 <= gap <= EDGE_EXPAND_PT and not wide(b):
                y0, y1 = min(y0, b["y0"]), max(y1, b["y1"])
        if y1 - y0 >= MIN_REGION_PT:
            regions.append({"y0": y0, "y1": y1, "height": y1 - y0,
                            "claimed": False, "caption": None})
    return regions


def _pair(cap, regions) -> dict | None:
    """Closest unclaimed region on the same page, if plausibly adjacent."""
    cap_y0, cap_y1 = cap["caption_bbox"][1], cap["caption_bbox"][3]
    best, best_gap = None, MAX_PAIR_GAP_PT
    for r in regions:
        if r["claimed"]:
            continue
        gap = min(abs(r["y1"] - cap_y0), abs(cap_y1 - r["y0"]))
        if gap < best_gap:
            best, best_gap = r, gap
    if best:
        best["claimed"] = True
        best["caption"] = cap
        best["caption_page"] = cap.get("page")
    return best


def _ink_x_range(pm, ys, xs, y0_pt, y1_pt):
    """Tight horizontal ink extent inside a vertical slice, read off pixels."""
    W, H = pm.width, pm.height
    y0, y1 = max(0, int(y0_pt / ys)), min(H, int(y1_pt / ys) + 1)
    if y1 <= y0:
        return None
    slice_ = np.frombuffer(pm.samples, dtype=np.uint8).reshape(H, W)[y0:y1] < INK_LEVEL
    cols = np.flatnonzero(slice_.any(axis=0))
    return None if not cols.size else (cols[0] * xs, cols[-1] * xs)


# Some journal fonts embed Greek letters as glyphs the text layer cannot map,
# so "HIF-2α" extracts as "HIF-2a" while other occurrences in the same file come
# out correctly. Copying the damaged form into a report produces a wrong gene or
# protein name. The reliable signal is *inconsistency*: the same stem appearing
# both with the Greek letter and with its Latin lookalike.
GREEK_LOOKALIKE = {"α": "a", "β": "b", "γ": "g", "δ": "d", "κ": "k",
                   "λ": "l", "μ": "u", "σ": "s", "τ": "t"}


def _greek_warnings(text: str, limit: int = 12) -> list[str]:
    warnings: list[str] = []
    for greek, latin in GREEK_LOOKALIKE.items():
        for m in re.finditer(r"([A-Za-z][A-Za-z0-9]{1,9})[-–]?" + re.escape(greek), text):
            stem = m.group(1)
            greek_form = f"{stem}-{greek}"
            latin_form = f"{stem}-{latin}"
            n_greek = text.count(greek_form)
            n_latin = len(re.findall(re.escape(latin_form) + r"\b", text, re.I))
            if n_latin:
                warnings.append(
                    f"{greek_form}（{n_greek} 处）与 {latin_form}（{n_latin} 处）同时出现"
                    f"——后者多半是希腊字母在文本层丢失所致，写报告时请以原图为准")
    # Unambiguous Greek-derived names. These fire even when the correct Greek
    # form never appears, because that is exactly the case the both-forms test
    # above misses: a document whose Greek glyphs all failed to extract shows
    # only the damaged spelling, so there is nothing to compare against.
    for bad, good in (("b-actin", "β-actin"), ("NF-kB", "NF-κB"), ("TGF-b", "TGF-β"),
                      ("TNF-a", "TNF-α"), ("IFN-g", "IFN-γ"), ("a-SMA", "α-SMA"),
                      ("b-catenin", "β-catenin"), ("HIF-1a", "HIF-1α"),
                      ("HIF-2a", "HIF-2α"), ("IL-1b", "IL-1β"), ("TGF-b1", "TGF-β1")):
        n = len(re.findall(r"\b" + re.escape(bad) + r"\b", text))
        if n:
            also = "（文中也出现了正确写法，属不一致）" if good in text else "（全文未见正确写法）"
            warnings.append(f"检测到 {bad}（{n} 处）{also}——若为希腊字母丢失，报告里应写作 {good}")
    seen, out = set(), []
    for w in warnings:
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out[:limit]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf", type=Path, help="path to the paper PDF")
    ap.add_argument("--out", type=Path, required=True, help="output directory")
    ap.add_argument("--page-dpi", type=int, default=130, help="full-page render DPI")
    ap.add_argument("--fig-dpi", type=int, default=200, help="cropped figure DPI")
    ap.add_argument("--pad", type=float, default=4.0, help="padding (points) around a crop")
    ap.add_argument("--max-pages", type=int, default=0, help="stop after N pages (0 = all)")
    ap.add_argument("--debug-page", type=int, default=0, help="print band structure for one page")
    args = ap.parse_args()

    if not args.pdf.exists():
        print(f"ERROR: no such file: {args.pdf}", file=sys.stderr)
        return 2

    out: Path = args.out
    pages_dir, figs_dir = out / "pages", out / "figures"
    pages_dir.mkdir(parents=True, exist_ok=True)
    figs_dir.mkdir(parents=True, exist_ok=True)

    doc = pymupdf.open(args.pdf)
    n_pages = len(doc)
    limit = min(n_pages, args.max_pages) if args.max_pages else n_pages
    print(f"PDF: {args.pdf.name}  pages={n_pages}  processing {limit}")

    # ---- pass 1: per-page bands, figure regions, captions
    page_info = []
    text_chunks = []
    for pno in range(limit):
        page = doc[pno]
        text = page.get_text("text")
        (pages_dir / f"page-{pno + 1:02d}.txt").write_text(text, encoding="utf-8")
        text_chunks.append(f"\n\n========== PAGE {pno + 1} ==========\n{text}")
        page.get_pixmap(dpi=args.page_dpi).save(pages_dir / f"page-{pno + 1:02d}.png")

        captions = []
        for block in page.get_text("blocks"):
            label = _is_caption(block[4].strip())
            if label:
                captions.append({"label": label, "caption": " ".join(block[4].split()),
                                 "page": pno + 1,
                                 "caption_bbox": [block[0], block[1], block[2], block[3]]})

        bands, pm, ys, xs = _bands(page)
        regions = _figure_regions(bands, page.rect)

        if args.debug_page == pno + 1:
            print(f"\n--- debug page {pno + 1}: {len(bands)} bands, "
                  f"{sum(1 for b in bands if b['graphic'])} graphic, {len(regions)} regions")
            for b in bands:
                print(f"    y {b['y0']:6.1f}-{b['y1']:6.1f} x {b['x0']:6.1f}-{b['x1']:6.1f} "
                      f"{'GFX' if b['graphic'] else '   '}")
            for r in regions:
                print(f"  REGION y {r['y0']:.1f}-{r['y1']:.1f} h={r['height']:.1f}")

        page_info.append({"pno": pno, "page": page, "pm": pm, "ys": ys, "xs": xs,
                          "captions": captions, "regions": regions})

    # ---- pass 2: pair captions with regions, allowing a one-page float
    for info in page_info:
        for cap in info["captions"]:
            _pair(cap, info["regions"])

    for i, info in enumerate(page_info):
        for cap in info["captions"]:
            if any(r["caption"] is cap for r in info["regions"]):
                continue
            # Figure floated to the previous page, caption at the top of this one
            # (Nature-family layout), or rarer: figure on the following page.
            for j in ([i - 1, i + 1] if i > 0 else [i + 1]):
                if not (0 <= j < len(page_info)):
                    continue
                free = [r for r in page_info[j]["regions"] if not r["claimed"]]
                if not free:
                    continue
                r = max(free, key=lambda r: r["height"])
                r["claimed"] = True
                r["caption"] = cap
                r["caption_page"] = cap.get("page")
                r["from_page"] = page_info[j]["pno"] + 1
                break

    # ---- pass 3: render the crops
    entries = []
    for info in page_info:
        page, pm, ys, xs = info["page"], info["pm"], info["ys"], info["xs"]
        for r in info["regions"]:
            cap = r.get("caption")
            if cap is None:
                continue          # a graphic with no caption anywhere: skip
            y0, y1 = r["y0"], r["y1"]
            rec = {"label": cap["label"], "page": r.get("from_page", info["pno"] + 1),
                   "caption_page": r.get("caption_page", info["pno"] + 1),
                   "caption": cap["caption"],
                   "height_pt": round(r["height"], 1), "figure_image": None, "crop_box": None}
            xr = _ink_x_range(pm, ys, xs, y0, y1)
            if xr:
                box = pymupdf.Rect(xr[0] - args.pad, y0 - args.pad,
                                   xr[1] + args.pad, y1 + args.pad) & page.rect
                if box.height > 20 and box.width > 60:
                    fname = f"{'-'.join(_norm(cap['label']))}__p{info['pno'] + 1:02d}.png"
                    page.get_pixmap(dpi=args.fig_dpi, clip=box).save(figs_dir / fname)
                    rec["figure_image"] = f"figures/{fname}"
                    rec["crop_box"] = [round(v, 1) for v in box]
            entries.append(rec)

    best: dict[str, dict] = {}
    for e in entries:
        k = "-".join(_norm(e["label"]))
        if k not in best or len(e["caption"]) > len(best[k]["caption"]):
            best[k] = e
    ordered = sorted(best.values(), key=lambda e: (_norm(e["label"])[1], _norm(e["label"])[0]))

    full_text = "".join(text_chunks)
    (out / "fulltext.txt").write_text(full_text, encoding="utf-8")

    # Data-hygiene notes: things about the *extracted text* that can mislead the
    # reader, independent of what the paper actually says.
    greek = _greek_warnings(full_text)
    supp_refs = len(re.findall(r"Supplementary\s+(?:Fig|Table|Data)", full_text, re.I))
    notes = [f"# 提取质量提示 — {args.pdf.name}", ""]
    if greek:
        notes += ["## 希腊字母可能在文本层丢失", ""]
        notes += [f"- {w}" for w in greek]
        notes += ["", "**报告里涉及这些名称时以外观原图为准，不要直接复制文本。**", ""]
    ext = len(re.findall(r"Extended\s+Data\s+(?:Fig|Table)", full_text, re.I))
    if supp_refs or ext:
        notes += ["## 补充材料引用", "",
                  f"- 正文引用 Supplementary/Extended Data 共 {supp_refs + ext} 处。"
                  f"本 PDF 是否包含这些补充材料，请以 `pages/` 下实际存在的页面为准；"
                  f"未包含的，报告中必须声明无法核实。", ""]
    (out / "notes.md").write_text("\n".join(notes), encoding="utf-8")
    if greek or supp_refs or ext:
        print(f"  ⚠ 提取质量提示 {len(greek)} 条 → {out / 'notes.md'}")

    lines = [f"# Figure captions — {args.pdf.name}", ""]
    for e in ordered:
        status = "" if e["figure_image"] else "  ⚠️ no image extracted"
        lines += [f"## {e['label']}  (figure on page {e['page']}, "
                  f"caption on page {e['caption_page']}){status}", "", e["caption"], ""]
    if not ordered:
        lines.append("_No figure captions matched. Likely a text-only manuscript, a "
                     "supplementary file, or a scanned PDF without a text layer._")
    (out / "captions.md").write_text("\n".join(lines), encoding="utf-8")

    manifest = {"pdf": str(args.pdf.resolve()), "n_pages": n_pages, "pages_processed": limit,
                "figures": ordered,
                "counts": {"captions_found": len(ordered),
                           "figures_cropped": len([e for e in ordered if e["figure_image"]])}}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                       encoding="utf-8")

    print(f"captions found : {manifest['counts']['captions_found']}")
    print(f"figures cropped: {manifest['counts']['figures_cropped']}")
    for e in ordered:
        flag = "" if e["figure_image"] else "   <-- NO IMAGE"
        cap = e["caption"][:50] + ("..." if len(e["caption"]) > 50 else "")
        print(f"  fig-p{e['page']:<3} cap-p{e['caption_page']:<3} {e['label']:<18} {cap}{flag}")
    print(f"\nwrote: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
