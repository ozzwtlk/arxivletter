"""arXiv HTML (LaTeXML) → structured document. Used when the LaTeX source cannot be parsed.

LaTeXML keeps the TeX of every formula in `<math alttext="…">`, the exact equation tags (`.ltx_tag_equation`),
section ids (S2.SS1) and bibliography ids (bib.bib12), so this fallback keeps equations, numbering, citations and
structure with high fidelity.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, NavigableString, Tag

from arxivletter.parsing.document import DocBuilder, reference_ids

SECTION_CLASSES = {"ltx_section": 1, "ltx_appendix": 1, "ltx_subsection": 2, "ltx_subsubsection": 3}


def _math(m: Tag) -> str:
    tex = (m.get("alttext") or "").strip()
    return f"${tex}$" if tex else ""


def _inline(el, out: list[str]) -> None:
    for ch in el.children:
        if isinstance(ch, NavigableString):
            out.append(str(ch))
            continue
        if not isinstance(ch, Tag):
            continue
        cls = ch.get("class") or []
        if ch.name == "math":
            out.append(_math(ch))
        elif ch.name == "cite":
            keys = [a["href"].split("#", 1)[1] for a in ch.select("a.ltx_ref") if a.get("href", "").startswith("#bib")]
            out.append(f"[cite:{','.join(keys)}]" if keys else ch.get_text())
        elif "ltx_note" in cls:  # footnote
            content = ch.select_one(".ltx_note_content")
            if content:
                inner: list[str] = []
                for x in content.children:
                    if isinstance(x, Tag) and "ltx_note_mark" in (x.get("class") or []):
                        continue
                    if isinstance(x, Tag) and "ltx_tag" in (x.get("class") or []):
                        continue
                    _inline(x, inner) if isinstance(x, Tag) else inner.append(str(x))
                out.append(" (Footnote: " + re.sub(r"\s+", " ", "".join(inner)).strip() + ")")
        elif ch.name in ("em", "i") or "ltx_font_italic" in cls:
            inner = []
            _inline(ch, inner)
            t = "".join(inner).strip()
            out.append(f"*{t}*" if t else "")
        elif ch.name in ("b", "strong") or "ltx_font_bold" in cls:
            inner = []
            _inline(ch, inner)
            t = "".join(inner).strip()
            out.append(f"**{t}**" if t else "")
        elif ch.name == "a" and "ltx_ref" in cls and not ch.get("href", "").startswith("http"):
            out.append(ch.get_text())
        elif ch.name in ("table",) and "ltx_equation" in " ".join(cls):
            continue
        else:
            _inline(ch, out)


def _text(el) -> str:
    out: list[str] = []
    _inline(el, out)
    return re.sub(r"[ \t\r\n]+", " ", "".join(out)).strip()


def parse(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    art = soup.select_one("article.ltx_document") or soup
    b = DocBuilder("html")
    t = art.select_one("h1.ltx_title_document")
    b.title = _text(t) if t else ""
    ab = art.select_one("div.ltx_abstract")
    if ab:
        h = ab.select_one("h6, .ltx_title_abstract")
        if h:
            h.decompose()
        b.abstract = " ".join(_text(p) for p in ab.select("p")) or _text(ab)

    def walk(node: Tag):
        for ch in node.children:
            if isinstance(ch, Tag):
                handle(ch)

    def handle(ch: Tag):
        if True:
            cls = ch.get("class") or []
            lvl = next((SECTION_CLASSES[c] for c in cls if c in SECTION_CLASSES), None)
            if ch.name == "section" and lvl:
                h = ch.find(re.compile("^h[1-6]$"), class_="ltx_title")
                num, title = None, ""
                if h:
                    tag = h.select_one(".ltx_tag")
                    if tag:
                        num = tag.get_text().strip().rstrip(".").removeprefix("Appendix").strip() or None
                        tag.decompose()
                    title = _text(h)
                b.section(title, num, lvl, ch.get("id"), "ltx_appendix" in cls)
                walk(ch)
            elif ch.name == "section" and "ltx_bibliography" in cls:
                for li in ch.select("li.ltx_bibitem"):
                    tag = li.select_one(".ltx_tag_bibitem")
                    n = re.sub(r"\D", "", tag.get_text()) if tag else ""
                    if tag:
                        tag.decompose()
                    links = " ".join(a.get("href", "") for a in li.select("a"))
                    txt = re.sub(r"\s+", " ", li.get_text(" ")).replace("External Links:", "").strip()
                    arxiv, doi = reference_ids(txt + " " + links)
                    b.references.append({"key": li.get("id"), "n": int(n) if n else len(b.references) + 1,
                                         "text": txt[:600], "arxiv": arxiv, "doi": doi})
            elif ch.name == "table" and ("ltx_equation" in cls or "ltx_equationgroup" in cls):
                maths = [m.get("alttext", "") for m in ch.select("math") if m.get("alttext")]
                tags = [x.get_text().strip().strip("()") for x in ch.select(".ltx_tag_equation")]
                if not maths:
                    return
                if len(maths) > 1:
                    rows = []
                    for tr in ch.select("tr"):
                        ms = [m.get("alttext", "") for m in tr.select("math") if m.get("alttext")]
                        if ms:
                            rows.append(" & ".join(ms) if len(ms) > 1 else ms[0])
                    latex = "\\begin{aligned}" + " \\\\ ".join(rows) + "\\end{aligned}"
                else:
                    latex = maths[0]
                number = tags[0] if len(tags) == 1 else (f"{tags[0]}–{tags[-1]}" if tags else None)
                b.equation(latex, number, ch.get("id"), "html")
            elif ch.name == "figure":
                kind = "table" if "ltx_table" in cls else "figure"
                cap = ch.select_one("figcaption")
                num = None
                if cap:
                    tag = cap.select_one(".ltx_tag")
                    if tag:
                        num = re.sub(r"^(Figure|Fig\.|Table|TABLE|FIG\.)\s*", "", tag.get_text().strip()).rstrip(":. ")
                        tag.decompose()
                b.figure(kind, _text(cap) if cap else "", num, ch.get("id"))
            elif ch.name == "div" and "ltx_para" in cls:
                for p in ch.find_all(["p", "table", "ul", "ol"], recursive=False):
                    if p.name == "table":
                        handle(p)
                    elif p.name in ("ul", "ol"):
                        b.paragraph("\n".join("- " + _text(li) for li in p.find_all("li", recursive=False)))
                    else:
                        b.paragraph(_text(p))
            elif ch.name in ("div", "section") and not ({"ltx_abstract", "ltx_authors", "ltx_dates", "ltx_pagination",
                                                         "ltx_classification", "ltx_keywords"} & set(cls)):
                walk(ch)

    walk(art)
    # cite keys in text are LaTeXML bib ids; references carry the same keys
    return b.build(numbering="exact (LaTeXML)")
