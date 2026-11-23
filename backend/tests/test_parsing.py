"""LaTeX / HTML / PDF parsing into the document model."""

import gzip
import io
import tarfile

from arxivletter.arxiv.source import unpack
from arxivletter.parsing import html, latex
from arxivletter.parsing.texutil import accents, read_group, split_math, strip_comments

from conftest import SAMPLE_TEX


def blocks(doc, kind=None):
    return [b for s in doc["sections"] for b in s["blocks"] if kind is None or b["type"] == kind]


def test_title_abstract_and_front_matter_removed(sample_doc):
    assert sample_doc["title"] == "Freeze-in of Dark Matter in Strong Fields"
    assert "Schwinger effect" in sample_doc["abstract"]
    text = " ".join(b.get("text", "") for b in blocks(sample_doc))
    assert "Somewhere University" not in text and "a@b.c" not in text


def test_revtex_section_numbers_and_html_anchors(sample_doc):
    secs = [(s["number"], s["title"], s["anchor"]) for s in sample_doc["sections"]]
    assert ("I", "Introduction", "S1") in secs
    assert ("II", "Model", "S2") in secs
    assert ("II A", "Caveats", "S2.SS1") in secs
    assert ("A", "Details", "A1") in secs


def test_env_shortcut_macros_equations_and_numbering(sample_doc):
    eqs = blocks(sample_doc, "equation")
    nums = [e["number"] for e in eqs]
    # \beq..\eeq (1); eqnarray with one \nonumber row (2); subequations align (3a–3b); revtex appendix (A1)
    assert nums == ["1", "2", "3a–3b", "A1"]
    assert eqs[0]["label"] == "eq:rate" and "\\label" not in eqs[0]["latex"]
    assert "\\begin{array}{rcl}" in eqs[1]["latex"]
    assert sample_doc["labels"]["eq:cd"]["number"] == "2"
    assert sample_doc["labels"]["eq:x"]["number"] == "3a"


def test_refs_cites_footnotes_and_text_conversion(sample_doc):
    intro = next(s for s in sample_doc["sections"] if s["title"] == "Introduction")
    t = intro["blocks"][0]["text"]
    assert "[cite:Schwinger:1951nm,Gies:2005]" in t
    assert "Sec. II" in t and "Eq. (1)" in t
    assert "(Footnote: A footnote with $x^2$.)" in t
    assert "$\\mchi$" in t  # author macros are kept; KaTeX gets them via `macros`
    assert "*emphasis*" in intro["blocks"][1]["text"] and "10%" in intro["blocks"][1]["text"]
    app = next(s for s in sample_doc["sections"] if s["title"] == "Details")
    assert "Fig. 1" in app["blocks"][1]["text"] and "Eq. (A1)" in app["blocks"][1]["text"]


def test_figure_bibliography_macros_and_provenance(sample_doc):
    figs = blocks(sample_doc, "figure")
    assert figs and figs[0]["number"] == "1" and "rate as a function" in figs[0]["text"]
    refs = sample_doc["references"]
    assert [r["key"] for r in refs] == ["Schwinger:1951nm", "Gies:2005"]
    assert refs[1]["arxiv"] == "hep-ph/0505099" and "Klingmüller" in refs[1]["text"]
    assert sample_doc["macros"]["\\mchi"] == "m_\\chi"
    assert sample_doc["macros"]["\\Tr"] == "\\operatorname{Tr}"
    assert "\\begin" not in sample_doc["macros"] and "\\beq" not in sample_doc["macros"]
    para = blocks(sample_doc, "paragraph")[0]
    assert para["src"]["file"] == "main.tex" and SAMPLE_TEX.split("\n")[para["src"]["line"] - 1].startswith("Dark matter")
    assert not sample_doc["warnings"]


def test_flatten_input_includegraphics_and_line_numbers():
    files = {
        "main.tex": "\\documentclass{article}\n\\begin{document}\n\\section{A}\n\\input{sec/b}\nAfter input.\n"
                    "\\includegraphics[width=0.4\\textwidth]{fig}\n\\end{document}\n",
        "sec/b.tex": "% comment\nIncluded text on line two.\n",
    }
    doc = latex.parse(files)
    ps = blocks(doc, "paragraph")
    inc = next(p for p in ps if "Included" in p["text"])
    assert inc["src"] == {"file": "sec/b.tex", "line": 2}
    assert not any("missing input" in w for w in doc["warnings"])


def test_numberwithin_section_and_tag():
    tex = ("\\documentclass{article}\\numberwithin{equation}{section}\\begin{document}\\section{One}"
           "\\begin{equation}a\\end{equation}\\begin{equation}b\\tag{$*$}\\end{equation}\\section{Two}"
           "\\begin{equation}c\\end{equation}\\[d\\]\\end{document}")
    doc = latex.parse({"m.tex": tex})
    assert [e["number"] for e in blocks(doc, "equation")] == ["1.1", "$*$", "2.1", None]


def test_tikz_inside_math_is_replaced():
    tex = ("\\documentclass{article}\\begin{document}\\section{S}\\begin{equation}\\begin{tikzpicture}\\draw (0,0)--(1,1);"
           "\\end{tikzpicture} = 1 \\end{equation}\\end{document}")
    eq = blocks(latex.parse({"m.tex": tex}), "equation")[0]
    assert "tikzpicture" not in eq["latex"] and "diagram" in eq["latex"]


def test_helpers():
    assert read_group("{a{b}c}d", 0) == ("a{b}c", 7)
    assert strip_comments("x % c\ny \\% z") == "x \ny \\% z"
    assert split_math("a $x$ b \\(y\\)") == [(False, "a "), (True, "x"), (False, " b "), (True, "y")]
    assert accents('M\\"uller \\\'Etienne') == "Müller Étienne"


def test_unpack_tar_gzip_and_pdf():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = SAMPLE_TEX.encode()
        ti = tarfile.TarInfo("paper.tex")
        ti.size = len(data)
        tf.addfile(ti, io.BytesIO(data))
    kind, files = unpack(buf.getvalue())
    assert kind == "tex" and "paper.tex" in files
    assert unpack(gzip.compress(SAMPLE_TEX.encode()))[0] == "tex"
    assert unpack(b"%PDF-1.5 ...")[0] == "pdf"


HTML = """<html><body><article class="ltx_document">
<h1 class="ltx_title ltx_title_document">A Title</h1>
<div class="ltx_abstract"><h6>Abstract</h6><p class="ltx_p">Abstract text.</p></div>
<section class="ltx_section" id="S1"><h2 class="ltx_title ltx_title_section"><span class="ltx_tag ltx_tag_section">1 </span>Intro</h2>
<div class="ltx_para"><p class="ltx_p">Mass <math alttext="m_{\\chi}" display="inline"></math> is small
<cite class="ltx_cite">[<a class="ltx_ref" href="#bib.bib1">1</a>]</cite>.</p></div>
<table class="ltx_equation ltx_eqn_table" id="S1.E1"><tr><td><math alttext="E=mc^{2}" display="block"></math></td>
<td><span class="ltx_tag ltx_tag_equation">(1)</span></td></tr></table>
</section>
<section class="ltx_bibliography" id="bib"><ul><li class="ltx_bibitem" id="bib.bib1"><span class="ltx_tag ltx_tag_bibitem">[1]</span>
A. B., arXiv:2401.00001</li></ul></section>
</article></body></html>"""


def test_html_latexml():
    doc = html.parse(HTML)
    assert doc["title"] == "A Title" and doc["abstract"] == "Abstract text."
    s = doc["sections"][0]
    assert (s["number"], s["title"], s["anchor"]) == ("1", "Intro", "S1")
    p = s["blocks"][0]["text"]
    assert "$m_{\\chi}$" in p and "[cite:bib.bib1]" in p
    eq = s["blocks"][1]
    assert eq["latex"] == "E=mc^{2}" and eq["number"] == "1"
    assert doc["references"][0]["arxiv"] == "2401.00001"
