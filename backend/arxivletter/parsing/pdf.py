"""PDF → structured document (last resort: PDF-only submissions without arXiv HTML).

Text comes from the PDF text layer (PyMuPDF). Section headings are detected from font size/boldness and numbering
patterns; display equations cannot be recovered as LaTeX, so they stay as plain text inside paragraphs and the
document is marked `math_fidelity: low` (the reader shows a warning, and the models are told).
"""

from __future__ import annotations

import re
from collections import Counter

from arxivletter.parsing.document import DocBuilder, reference_ids

HEADING = re.compile(r"^(?:(?P<num>(?:[IVX]+|\d+|[A-H])(?:\.\d+){0,2})\.?\s+)(?P<title>[A-Z][^\n]{2,90})$")
REFS = re.compile(r"^(references|bibliography)$", re.I)


def parse(pdf_bytes: bytes) -> dict:
    import fitz  # PyMuPDF

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    b = DocBuilder("pdf")
    lines: list[tuple[str, float, bool, int]] = []
    sizes: Counter = Counter()
    for pno, page in enumerate(doc):
        for blk in page.get_text("dict")["blocks"]:
            for ln in blk.get("lines", []):
                spans = [s for s in ln["spans"] if s["text"].strip()]
                if not spans:
                    continue
                text = "".join(s["text"] for s in spans).strip()
                size = max(s["size"] for s in spans)
                bold = all("Bold" in s["font"] or s["flags"] & 16 for s in spans)
                lines.append((text, size, bold, pno + 1))
                sizes[round(size)] += len(text)
            lines.append(("", 0, False, pno + 1))  # block break
    body = sizes.most_common(1)[0][0] if sizes else 10
    meta = doc.metadata or {}
    b.title = (meta.get("title") or "").strip()
    para: list[str] = []
    page_of_para = 1
    in_refs = False
    ref_buf: list[str] = []

    def flush():
        nonlocal para
        if para:
            text = re.sub(r"-\n(?=[a-z])", "", "\n".join(para))
            text = re.sub(r"\s*\n\s*", " ", text)
            if in_refs:
                ref_buf.append(text)
            else:
                b.paragraph(text, {"file": "pdf", "line": page_of_para})
            para = []

    for text, size, bold, pno in lines:
        if not text:
            flush()
            continue
        if re.fullmatch(r"\d{1,3}", text):  # page number
            continue
        m = HEADING.match(text)
        is_head = (m and (bold or size > body + 0.5) and len(text) < 100) or (
            REFS.match(text) and (bold or size >= body))
        if is_head:
            flush()
            if REFS.match(text):
                in_refs = True
                continue
            in_refs = False
            num = m.group("num") if m else None
            level = 1 + (num.count(".") if num else 0)
            b.section(m.group("title") if m else text, num, min(level, 3))
            continue
        if not para:
            page_of_para = pno
        para.append(text)
    flush()
    joined = " ".join(ref_buf)
    for i, item in enumerate(re.split(r"\s*\[(\d{1,3})\]\s+", joined)[1:][1::2]):
        arxiv, doi = reference_ids(item)
        b.references.append({"key": f"ref{i + 1}", "n": i + 1, "text": item[:600], "arxiv": arxiv, "doi": doi})
    b.warnings.append("PDF text layer: display equations are not available as LaTeX")
    return b.build(math_fidelity="low", pages=len(doc))


def abstract_only(title: str, abstract: str, reason: str) -> dict:
    b = DocBuilder("abstract", title=title, abstract=abstract)
    b.section("Abstract", None, 1, None)
    b.paragraph(abstract)
    b.warnings.append(f"full text unavailable: {reason}")
    return b.build(math_fidelity="abstract only")
