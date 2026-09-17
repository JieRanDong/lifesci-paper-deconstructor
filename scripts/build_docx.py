#!/usr/bin/env python3
"""Render a markdown report into a Chinese-typeset Word document.

The report is written as markdown because that is the easiest thing for a model
to draft and revise; this script turns it into the .docx the reader actually
wants. Bundled so every report gets the same typography instead of whatever
ad-hoc script the moment produced.

Supported markdown:
  # / ## / ### / ####      headings (Chinese heading fonts applied)
  **bold**, *italic*, `code`
  - / *                    bullet list
  1.                       numbered list
  > quote                  shaded callout (good for the one-line conclusion)
  ![alt](path)             centered image + caption line
  | a | b |                pipe table with a |---|---| separator row
  ---                      horizontal rule
  blank-line separated     paragraphs

Usage:
  uv run --with python-docx python build_docx.py report.md -o report.docx
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import docx
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

BODY_LATIN = "Cambria"
BODY_CJK = "宋体"
HEAD_LATIN = "Calibri"
HEAD_CJK = "微软雅黑"
MONO = "Consolas"
MAX_IMAGE_WIDTH_IN = 6.2


def _set_style_fonts(style, latin: str, cjk: str, size: int, bold: bool = False,
                     color: tuple[int, int, int] | None = None):
    style.font.name = latin
    style.font.size = Pt(size)
    style.font.bold = bold
    if color:
        style.font.color.rgb = RGBColor(*color)
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = rpr.makeelement(qn("w:rFonts"), {})
        rpr.append(rfonts)
    rfonts.set(qn("w:ascii"), latin)
    rfonts.set(qn("w:hAnsi"), latin)
    rfonts.set(qn("w:eastAsia"), cjk)   # without this, Word falls back badly


def _init_styles(doc):
    _set_style_fonts(doc.styles["Normal"], BODY_LATIN, BODY_CJK, 10.5)
    doc.styles["Normal"].paragraph_format.space_after = Pt(6)
    doc.styles["Normal"].paragraph_format.line_spacing = 1.35
    for name, size in (("Heading 1", 16), ("Heading 2", 13.5),
                       ("Heading 3", 11.5), ("Heading 4", 10.5)):
        st = doc.styles[name]
        _set_style_fonts(st, HEAD_LATIN, HEAD_CJK, size, bold=True,
                         color=(0x1F, 0x38, 0x64))
        st.paragraph_format.space_before = Pt(14 if size >= 13.5 else 10)
        st.paragraph_format.space_after = Pt(6)


INLINE_RE = re.compile(r"(\*\*.+?\*\*|\*[^*]+?\*|`[^`]+?`)")


def _add_runs(par, text: str):
    """Emit runs for **bold**, *italic* and `code` inside one paragraph."""
    for part in INLINE_RE.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            r = par.add_run(part[2:-2]); r.bold = True
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            r = par.add_run(part[1:-1]); r.italic = True
        elif part.startswith("`") and part.endswith("`") and len(part) > 2:
            r = par.add_run(part[1:-1]); r.font.name = MONO
            r.element.rPr.rFonts.set(qn("w:eastAsia"), MONO)
        else:
            par.add_run(part)
    for r in par.runs:
        r.font.name = r.font.name or BODY_LATIN
        rpr = r._element.get_or_add_rPr()
        rf = rpr.find(qn("w:rFonts"))
        if rf is None:
            rf = rpr.makeelement(qn("w:rFonts"), {}); rpr.append(rf)
        rf.set(qn("w:eastAsia"), MONO if r.font.name == MONO else BODY_CJK)


IMG_RE = re.compile(r"^!\[(?P<alt>.*?)\]\((?P<path>.+?)\)\s*$")
NUM_RE = re.compile(r"^\d+[.)]\s+")
TABLE_SEP_RE = re.compile(r"^\|?[\s:|-]+\|[\s:|-]*$")


def _flush_table(doc, rows: list[list[str]]):
    if not rows:
        return
    ncols = max(len(r) for r in rows)
    t = doc.add_table(rows=0, cols=ncols)
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, row in enumerate(rows):
        cells = t.add_row().cells
        for j in range(ncols):
            text = row[j] if j < len(row) else ""
            par = cells[j].paragraphs[0]
            _add_runs(par, text)
            par.paragraph_format.space_after = Pt(2)
            if i == 0:
                for r in par.runs:
                    r.bold = True
    doc.add_paragraph()


def build(md_path: Path, out_path: Path, base_dir: Path | None = None):
    text = md_path.read_text(encoding="utf-8")
    base = base_dir or md_path.parent
    doc = docx.Document()
    _init_styles(doc)

    lines = text.splitlines()
    i = 0
    table_rows: list[list[str]] = []

    def flush():
        nonlocal table_rows
        if table_rows:
            _flush_table(doc, table_rows)
            table_rows = []

    while i < len(lines):
        line = lines[i].rstrip()
        stripped = line.strip()

        if not stripped:
            flush()
            i += 1
            continue

        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if not TABLE_SEP_RE.match(stripped):
                table_rows.append(cells)
            i += 1
            continue
        flush()

        m = IMG_RE.match(stripped)
        if m:
            img = (base / m.group("path")).resolve()
            if img.exists():
                p = doc.add_paragraph()
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                p.paragraph_format.space_before = Pt(6)
                p.paragraph_format.space_after = Pt(2)
                p.add_run().add_picture(str(img), width=Inches(MAX_IMAGE_WIDTH_IN))
                if m.group("alt"):
                    cap = doc.add_paragraph()
                    cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    cap.paragraph_format.space_after = Pt(10)
                    r = cap.add_run(m.group("alt"))
                    r.italic = True
                    r.font.size = Pt(9)
                    r.font.color.rgb = RGBColor(0x59, 0x59, 0x59)
                    r.font.name = BODY_LATIN
                    rpr = r._element.get_or_add_rPr()
                    rf = rpr.find(qn("w:rFonts"))
                    if rf is None:
                        rf = rpr.makeelement(qn("w:rFonts"), {})
                        rpr.append(rf)
                    rf.set(qn("w:eastAsia"), BODY_CJK)
            else:
                warn = doc.add_paragraph()
                r = warn.add_run(f"[缺图: {m.group('path')}]")
                r.italic = True
                r.font.color.rgb = RGBColor(0xC0, 0x00, 0x00)
            i += 1
            continue

        if re.match(r"^-{3,}$", stripped):
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(2)
            p.paragraph_format.space_after = Pt(2)
            pr = p._p.get_or_add_pPr()
            bd = pr.makeelement(qn("w:pBdr"), {})
            bottom = bd.makeelement(qn("w:bottom"), {})
            bottom.set(qn("w:val"), "single"); bottom.set(qn("w:sz"), "6")
            bottom.set(qn("w:color"), "BFBFBF")
            bd.append(bottom); pr.append(bd)
            i += 1
            continue

        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            body = stripped[level:].strip()
            doc.add_heading(body, level=min(max(level, 1), 4))
            i += 1
            continue

        if stripped.startswith("> "):
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Inches(0.25)
            pr = p._p.get_or_add_pPr()
            bd = pr.makeelement(qn("w:pBdr"), {})
            left = bd.makeelement(qn("w:left"), {})
            left.set(qn("w:val"), "single"); left.set(qn("w:sz"), "18")
            left.set(qn("w:color"), "4472C4"); left.set(qn("w:space"), "8")
            bd.append(left); pr.append(bd)
            _add_runs(p, stripped[2:])
            i += 1
            continue

        if re.match(r"^[-*]\s+", stripped):
            p = doc.add_paragraph(style="List Bullet")
            p.paragraph_format.space_after = Pt(3)
            _add_runs(p, re.sub(r"^[-*]\s+", "", stripped))
            i += 1
            continue

        if NUM_RE.match(stripped):
            p = doc.add_paragraph(style="List Number")
            p.paragraph_format.space_after = Pt(3)
            _add_runs(p, NUM_RE.sub("", stripped))
            i += 1
            continue

        p = doc.add_paragraph()
        _add_runs(p, stripped)
        i += 1

    flush()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    return out_path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("markdown", type=Path, help="report markdown file")
    ap.add_argument("-o", "--out", type=Path, help="output .docx (default: alongside)")
    ap.add_argument("--base-dir", type=Path, default=None,
                    help="directory image paths are relative to (default: md's folder)")
    args = ap.parse_args()

    if not args.markdown.exists():
        print(f"ERROR: no such file: {args.markdown}", file=sys.stderr)
        return 2
    out = args.out or args.markdown.with_suffix(".docx")
    build(args.markdown, out, args.base_dir)
    n_images = len(re.findall(r"!\[", args.markdown.read_text(encoding="utf-8")))
    print(f"wrote {out}  ({n_images} image reference(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
