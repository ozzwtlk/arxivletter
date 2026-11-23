"""LaTeX source → structured document (parsing/document.py).

Pipeline: pick the main file → flatten \\input/\\include (recording file + line of every position) → strip
comments → collect macros → title/abstract → scan the body for sections, display math, floats and the
bibliography → paragraphs converted to Markdown with $…$ math, [cite:…] citations and resolved \\ref numbers.

Equation numbers are *reconstructed* from the source (document class conventions: revtex → 1, 2, … and A1 in
appendices; jheppub or \\numberwithin{equation}{section} → 2.3; \\tag, subequations, starred environments and
\\nonumber are honoured). They match the PDF in the vast majority of papers; the reader marks them as reconstructed.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from arxivletter.parsing.document import DocBuilder, reference_ids
from arxivletter.parsing.texutil import (accents, clean_math, read_args, read_group, skip_ws, split_math,
                                         strip_comments, unescaped)

log = logging.getLogger("arxivletter.latex")

MAX_DEPTH = 12
DISPLAY_ENVS = ("equation", "equation*", "align", "align*", "eqnarray", "eqnarray*", "gather", "gather*",
                "multline", "multline*", "flalign", "flalign*", "alignat", "alignat*", "displaymath", "dmath",
                "dmath*")
FLOAT_ENVS = ("figure", "figure*", "table", "table*", "wrapfigure", "sidewaysfigure", "sidewaystable")
OTHER_ENVS = ("thebibliography", "abstract", "acknowledgments", "acknowledgements", "subequations", "appendices",
              "keywords")
SECTION_LEVEL = {"part": 0, "chapter": 0, "section": 1, "subsection": 2, "subsubsection": 3}
THEOREMS = ("theorem", "lemma", "proposition", "corollary", "definition", "remark", "example", "conjecture",
            "claim", "proof", "assumption", "hypothesis", "observation", "note")

_ENV_ALT = "|".join(re.escape(e) for e in DISPLAY_ENVS + FLOAT_ENVS + OTHER_ENVS)
STRUCT = re.compile(
    r"\\(?P<sec>part|chapter|section|subsection|subsubsection|paragraph|subparagraph)(?P<star>\*?)\s*(?=[\[{])"
    r"|\\begin\s*\{(?P<env>" + _ENV_ALT + r")\}"
    r"|\\end\s*\{(?P<endenv>subequations|acknowledgments|acknowledgements|appendices)\}"
    r"|\\(?P<appendix>appendix)(?![A-Za-z])"
    r"|\\(?P<ack>acknowledgments|acknowledgements|acknowledgment)(?![A-Za-z])"
    r"|\\(?P<bib>bibliography)\s*\{"
    r"|(?P<dmath>\\\[|\$\$)"
    r"|\\(?P<enddoc>end\s*\{document\})"
)
INPUT_RE = re.compile(r"\\(?:input|include|subfile|import\s*\{[^}]*\})(?![A-Za-z@])\s*(?:\{([^}]+)\}|([^\s{}\\\[]+))")


# ---------------------------------------------------------------- archive → main file, flattening
def pick_main(files: dict[str, str]) -> str | None:
    tex = {k: v for k, v in files.items() if k.lower().endswith((".tex", ".ltx", ".latex")) or "." not in k.split("/")[-1]}
    cands = [k for k, v in tex.items() if re.search(r"\\documentclass|\\documentstyle", strip_comments(v))]
    with_doc = [k for k in cands if re.search(r"\\begin\s*\{document\}", strip_comments(tex[k]))]
    cands = with_doc or cands
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0]
    included = set()
    for k in cands:
        for m in INPUT_RE.finditer(strip_comments(tex[k])):
            included.add((m.group(1) or m.group(2)).strip())
    top = [k for k in cands if k.rsplit(".", 1)[0] not in included and k not in included]
    pref = [k for k in (top or cands) if re.search(r"(^|/)(main|ms|paper|article|manuscript)\.tex$", k, re.I)]
    pool = pref or top or cands
    return max(pool, key=lambda k: len(tex[k]))


@dataclass
class Flat:
    text: str
    markers: list[tuple[int, str, int]]  # (position in flat text, file, line at that position)

    def where(self, pos: int) -> dict:
        lo, hi, best = 0, len(self.markers) - 1, 0
        while lo <= hi:
            mid = (lo + hi) // 2
            if self.markers[mid][0] <= pos:
                best, lo = mid, mid + 1
            else:
                hi = mid - 1
        mpos, f, line = self.markers[best]
        return {"file": f, "line": line + self.text.count("\n", mpos, pos)}


def _resolve(files: dict[str, str], name: str, parent: str) -> str | None:
    name = name.strip().strip('"')
    base = parent.rsplit("/", 1)[0] + "/" if "/" in parent else ""
    for cand in (name, name + ".tex", base + name, base + name + ".tex", name.lstrip("./"), name.lstrip("./") + ".tex"):
        if cand in files:
            return cand
    low = {k.lower(): k for k in files}
    for cand in (name.lower(), name.lower() + ".tex"):
        if cand in low:
            return low[cand]
    return None


ENV_MACRO_DEF = re.compile(
    r"\\(?:re)?newcommand\*?\s*\{?(\\[A-Za-z@]+)\}?\s*\{\s*(\\(?:begin|end)\s*\{[A-Za-z*]+\}(?:\s*\\(?:label|nonumber)\{?[^}]*\}?)?)\s*\}"
    r"|\\def\s*(\\[A-Za-z@]+)\s*\{\s*(\\(?:begin|end)\s*\{[A-Za-z*]+\})\s*\}")


DEF_BEFORE = re.compile(r"\\(?:(?:re)?newcommand|providecommand|DeclareRobustCommand|def|let)\*?\s*\{?\s*$")


def env_macros(files: dict[str, str]) -> dict[str, str]:
    """Shortcuts such as \\beq → \\begin{equation}, \\eea → \\end{eqnarray} (very common in hep papers)."""
    out = {}
    for k, v in files.items():
        if k.lower().endswith((".tex", ".sty", ".ltx")) or "." not in k:
            for m in ENV_MACRO_DEF.finditer(strip_comments(v)):
                name, body = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
                out[name] = body
    return out


def flatten(files: dict[str, str], main: str, warnings: list[str]) -> Flat:
    parts: list[str] = []
    emac = env_macros(files)
    emac_re = re.compile("(" + "|".join(re.escape(k) for k in sorted(emac, key=len, reverse=True)) + r")(?![A-Za-z@])") if emac else None
    markers: list[tuple[int, str, int]] = []
    length = 0

    def rec(name: str, depth: int, stack: tuple[str, ...]):
        nonlocal length
        text = strip_comments(files[name])
        if emac_re:  # expand uses, never the definitions themselves (\\newcommand\\be{...} must stay intact)
            text = emac_re.sub(lambda m: m.group(0) if DEF_BEFORE.search(text, max(0, m.start() - 40), m.start())
                               else emac[m.group(1)], text)
        pos = 0
        for m in INPUT_RE.finditer(text):
            seg = text[pos:m.start()]
            markers.append((length, name, text.count("\n", 0, pos) + 1))
            parts.append(seg)
            length += len(seg)
            child = _resolve(files, m.group(1) or m.group(2), name)
            if child and child not in stack and depth < MAX_DEPTH and not child.lower().endswith((".sty", ".cls")):
                rec(child, depth + 1, stack + (child,))
            elif not child:
                if not (m.group(1) or m.group(2) or "").endswith((".sty", ".cls", ".bbl")):
                    warnings.append(f"missing input file {(m.group(1) or m.group(2))!r}")
            pos = m.end()
        markers.append((length, name, text.count("\n", 0, pos) + 1))
        parts.append(text[pos:])
        length += len(text) - pos

    rec(main, 0, (main,))
    return Flat("".join(parts), markers)


# ---------------------------------------------------------------- macros
def collect_macros(pre: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in re.finditer(r"\\(?:re)?newcommand\*?|\\providecommand\*?|\\DeclareRobustCommand\*?", pre):
        j = skip_ws(pre, m.end())
        if j >= len(pre):
            continue
        if pre[j] == "{":
            name, j = read_group(pre, j)
            name = name.strip()
        else:
            nm = re.match(r"\\[A-Za-z@]+|\\.", pre[j:])
            if not nm:
                continue
            name, j = nm.group(0), j + nm.end()
        j = skip_ws(pre, j)
        nargs, has_default = 0, False
        if j < len(pre) and pre[j] == "[":
            n, j = read_group(pre, j, "[", "]")
            nargs = int(n) if n.strip().isdigit() else 0
            j = skip_ws(pre, j)
            if j < len(pre) and pre[j] == "[":
                _, j = read_group(pre, j, "[", "]")
                has_default = True
                j = skip_ws(pre, j)
        if j < len(pre) and pre[j] == "{" and re.fullmatch(r"\\[A-Za-z@]+", name) and not has_default:
            body, _ = read_group(pre, j)
            out[name] = clean_math(body.replace("\\xspace", "")) if nargs == 0 else body.replace("\\xspace", "")
    for m in re.finditer(r"\\def\s*(\\[A-Za-z@]+)((?:#\d)*)\s*\{", pre):
        body, _ = read_group(pre, m.end() - 1)
        out.setdefault(m.group(1), body.replace("\\xspace", ""))
    for m in re.finditer(r"\\DeclareMathOperator(\*?)\s*\{(\\[A-Za-z]+)\}\s*\{", pre):
        body, _ = read_group(pre, m.end() - 1)
        out[m.group(2)] = (r"\operatorname*{" if m.group(1) else r"\operatorname{") + body + "}"
    # drop definitions KaTeX would choke on or that redefine structure
    bad = re.compile(r"\\(?:begin|end|section|newcommand|def|let|makeatletter|ifx|else|fi|csname|expandafter|"
                     r"usepackage|hbox|vbox|setlength|par\b|footnote|cite|ref|label)")
    reserved = {r"\begin", r"\end", r"\left", r"\right", r"\item", r"\label", r"\ref", r"\eqref", r"\cite",
                r"\section", r"\title", r"\author", r"\maketitle", r"\frac", r"\text", r"\mathrm", r"\\"}
    return {k: v for k, v in out.items() if not bad.search(v) and len(v) < 400 and k not in reserved}


MATHY = re.compile(r"[\^_]|\\(?:math|frac|text|left|right|alpha|beta|gamma|delta|epsilon|mu|nu|chi|phi|psi|sigma|"
                   r"lambda|Lambda|Omega|omega|rho|tau|theta|pi|sqrt|bar|hat|tilde|langle|rangle|partial|cal|rm|bf|it|"
                   r"operatorname|sim|lesssim|gtrsim|times|odot|ell|hbar|dagger|prime|equiv|propto|approx)")


# ---------------------------------------------------------------- text mode → Markdown
def _cite(m_keys: str) -> str:
    keys = [k.strip() for k in m_keys.split(",") if k.strip()]
    return f"[cite:{','.join(keys)}]" if keys else ""


class TextConverter:
    def __init__(self, macros: dict[str, str]):
        self.macros0 = {k: v for k, v in macros.items() if "#" not in v}
        self.footnotes: list[str] = []

    def expand_text_macros(self, s: str, depth: int = 0) -> str:
        if not self.macros0 or depth > 3:
            return s

        def rep(m: re.Match) -> str:
            d = self.macros0.get(m.group(1))
            if d is None:
                return m.group(0)
            d = self.expand_text_macros(d, depth + 1)
            return f"${d}$" if MATHY.search(d) and "$" not in d else d
        return re.sub(r"(\\[A-Za-z@]+)(?![A-Za-z@])(?:\{\})?", rep, s)

    def convert(self, s: str) -> str:
        """Text-mode LaTeX → Markdown; inline math kept verbatim (cleaned) between $…$."""
        s = self._footnotes(s)  # before splitting: footnotes contain math
        pieces = []
        for is_math, p in split_math(s):
            if is_math:
                p = clean_math(p)
                pieces.append(f"${p}$" if p else "")
            else:
                pieces.append(self._text(p))
        out = "".join(pieces)
        out = re.sub(r"[ \t]+", " ", out)
        out = re.sub(r" *\n *", "\n", out)
        out = re.sub(r"\n{2,}", "\n", out)
        return out.strip()

    def _text(self, t: str) -> str:
        t = self.expand_text_macros(t)
        if "$" in t:  # macro expansion produced math: run the splitter again
            return "".join((f"${clean_math(p)}$" if m else self._plain(p)) for m, p in split_math(t))
        return self._plain(t)

    def _plain(self, t: str) -> str:
        # references and citations first (their arguments must not be mangled)
        t = re.sub(r"\\(?:cite[a-zA-Z]*|onlinecite|citenum|Cite[a-zA-Z]*)\*?\s*(?:\[[^\]]*\]\s*){0,2}\{([^}]*)\}",
                   lambda m: _cite(m.group(1)), t)
        t = re.sub(r"\\(eqref|ref|autoref|cref|Cref|pageref)\*?\s*\{([^}]*)\}", lambda m: f"\x01{m.group(1)}:{m.group(2)}\x02", t)
        t = re.sub(r"\\label\s*\{[^}]*\}", "", t)
        t = re.sub(r"\\href\s*\{([^}]*)\}\s*\{([^}]*)\}", r"[\2](\1)", t)
        t = re.sub(r"\\url\s*\{([^}]*)\}", r"<\1>", t)
        # lists and theorem-like environments
        t = re.sub(r"\\begin\s*\{(?:itemize|enumerate|description|list)\}(?:\[[^\]]*\])?", "\n", t)
        t = re.sub(r"\\end\s*\{(?:itemize|enumerate|description|list)\}", "\n", t)
        t = re.sub(r"\\item\s*\[([^\]]*)\]", r"\n- **\1** ", t)
        t = re.sub(r"\\item\b", "\n- ", t)
        t = re.sub(r"\\begin\s*\{(" + "|".join(THEOREMS) + r")\*?\}(?:\[([^\]]*)\])?",
                   lambda m: f"\n**{m.group(1).capitalize()}{(' (' + m.group(2) + ')') if m.group(2) else ''}.** ", t)
        t = re.sub(r"\\begin\s*\{tabular[x*]?\}.*?\\end\s*\{tabular[x*]?\}", " [table] ", t, flags=re.S)
        t = re.sub(r"\\(?:begin|end)\s*\{[A-Za-z*]+\}(?:\{[^}]*\})?", " ", t)
        # formatting
        for _ in range(3):  # nested
            t = re.sub(r"\\(?:emph|textit|textsl|it|em)\s*\{([^{}]*)\}", r"*\1*", t)
            t = re.sub(r"\{\\(?:em|it|itshape|sl)\s+([^{}]*)\}", r"*\1*", t)
            t = re.sub(r"\\textbf\s*\{([^{}]*)\}", r"**\1**", t)
            t = re.sub(r"\{\\(?:bf|bfseries)\s+([^{}]*)\}", r"**\1**", t)
            t = re.sub(r"\\texttt\s*\{([^{}]*)\}", r"`\1`", t)
            t = re.sub(r"\\(?:textrm|textsc|textsf|textup|textnormal|text|mbox|hbox|makebox|uppercase|MakeUppercase|"
                       r"underline|uline|textmd|ensuremath|mathrm|small|footnotesize)\s*\{([^{}]*)\}", r"\1", t)
            t = re.sub(r"\\(?:textcolor|colorbox)\s*\{[^{}]*\}\s*\{([^{}]*)\}", r"\1", t)
        t = re.sub(r"\\color\s*\{[^{}]*\}", "", t)
        t = accents(t)
        repl = [(r"\\(?:ldots|dots|cdots)(?![A-Za-z])", "…"), (r"---", "—"), (r"--", "–"), (r"``", "“"), (r"''", "”"),
                (r"\\%", "%"), (r"\\&", "&"), (r"\\_", "_"), (r"\\#", "#"), (r"\\\$", "\\$"), (r"\\textbackslash\b", "\\\\"),
                (r"(?<!\\)~", " "), (r"\\\\(?:\*)?(?:\[[^\]]*\])?", "\n"), (r"\\(?:newline|linebreak|par)\b", "\n"),
                (r"\\[,;:! ]", " "), (r"\\(?:LaTeX|TeX)\b", "LaTeX"), (r"\\(?:ie)\b", "i.e."), (r"\\(?:eg)\b", "e.g."),
                (r"\\(?:etal)\b", "et al."), (r"\\S(?![A-Za-z])", "§"), (r"\\textdegree\b", "°")]
        for a, b in repl:
            t = re.sub(a, b, t)
        t = re.sub(r"\\(?:noindent|indent|medskip|bigskip|smallskip|centering|raggedright|clearpage|newpage|"
                   r"maketitle|tableofcontents|vfill|hfill|null|footnotemark|protect|nopagebreak|pagebreak|"
                   r"normalsize|small|large|Large|footnotesize|scriptsize|selectfont|bf|it|rm|sc|em|sl|"
                   r"bfseries|itshape|mdseries|upshape|normalfont)(?![A-Za-z])\s*", "", t)
        t = re.sub(r"\\(?:vspace|hspace|vskip|hskip|addvspace|setlength|addtolength|setcounter|addtocounter|"
                   r"bibliographystyle|pagestyle|thispagestyle|enlargethispage|graphicspath|input|include|"
                   r"includegraphics|newcolumntype|nocite)\*?\s*(?:\[[^\]]*\])?(?:\{[^{}]*\})*", "", t)
        # any other command with a braced argument: keep the argument text; bare unknown commands: drop
        for _ in range(2):
            t = re.sub(r"\\[A-Za-z@]+\*?\s*(?:\[[^\]]*\])?\{([^{}]*)\}", r"\1", t)
        t = re.sub(r"\\[A-Za-z@]+\*?", "", t)
        t = t.replace("{", "").replace("}", "")
        return t

    def _footnotes(self, t: str) -> str:
        out, i = [], 0
        for m in re.finditer(r"\\footnote(?:text)?\s*(?:\[[^\]]*\])?\s*\{", t):
            if m.start() < i:
                continue
            body, end = read_group(t, m.end() - 1)
            out.append(t[i:m.start()])
            out.append(" (Footnote: " + body.strip() + ")")
            i = end
        out.append(t[i:])
        return "".join(out)


# ---------------------------------------------------------------- numbering
ROMAN = ["", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII", "XIII", "XIV", "XV", "XVI",
         "XVII", "XVIII", "XIX", "XX"]


class Numbering:
    def __init__(self, style: str, eq_by_section: bool):
        self.style, self.eq_by_section = style, eq_by_section
        self.sec = [0, 0, 0, 0]
        self.appendix = False
        self.eq = 0
        self.fig = 0
        self.tab = 0
        self.sub_base: str | None = None
        self.sub_n = 0
        self.html_num = [0, 0, 0]
        self.html_star = [0, 0, 0]
        self.html_path: list[str] = []
        self._html_appendix = False

    def section(self, level: int, starred: bool) -> tuple[str | None, str]:
        lvl = max(1, min(level, 3))
        # LaTeXML (arXiv HTML) anchors: S2, S2.SS1, S2.SS1.SSS3; starred units Sx1, S5.SS1.SSSx1; appendices A1
        if self.appendix and not self._html_appendix:
            self._html_appendix = True
            self.html_num, self.html_star = [0, 0, 0], [0, 0, 0]
        if starred:
            self.html_star[lvl - 1] += 1
        else:
            self.html_num[lvl - 1] += 1
        for k in range(lvl, 3):
            self.html_num[k] = self.html_star[k] = 0
        prefix = ("A" if self.appendix else "S") if lvl == 1 else "S" * lvl
        token = prefix + ("x" + str(self.html_star[lvl - 1]) if starred else str(self.html_num[lvl - 1]))
        self.html_path = self.html_path[:lvl - 1] + [token]
        anchor = ".".join(self.html_path)
        if starred:
            return None, anchor
        self.sec[lvl] += 1
        for k in range(lvl + 1, 4):
            self.sec[k] = 0
        if lvl == 1 and (self.eq_by_section or self.appendix):  # appendix equations restart: (A1), (B1) / (A.1)
            self.eq = 0
        top = (chr(64 + self.sec[1]) if self.sec[1] <= 26 else str(self.sec[1])) if self.appendix else (
            ROMAN[self.sec[1]] if self.style == "revtex" and self.sec[1] < len(ROMAN) else str(self.sec[1]))
        if lvl == 1:
            return top, anchor
        if self.style == "revtex":
            parts = [top, chr(64 + self.sec[2]) if self.sec[2] <= 26 else str(self.sec[2])]
            if lvl == 3:
                parts.append(str(self.sec[3]))
            return " ".join(parts), anchor
        return ".".join([top] + [str(self.sec[k]) for k in range(2, lvl + 1)]), anchor

    def next_eq(self, tag: str | None = None) -> str:
        if tag:
            return tag
        if self.sub_base is not None:
            self.sub_n += 1
            return f"{self.sub_base}{chr(96 + self.sub_n)}"
        self.eq += 1
        return self._fmt(self.eq)

    def _fmt(self, n: int) -> str:
        if self.appendix and self.sec[1]:
            letter = chr(64 + self.sec[1])
            return f"{letter}.{n}" if self.eq_by_section and self.style != "revtex" else f"{letter}{n}"
        if self.eq_by_section and self.sec[1]:
            return f"{self.sec[1]}.{n}"
        return str(n)

    def begin_sub(self):
        self.eq += 1
        self.sub_base, self.sub_n = self._fmt(self.eq), 0

    def end_sub(self):
        self.sub_base = None

    def next_fig(self, kind: str) -> str:
        if kind == "figure":
            self.fig += 1
            return str(self.fig)
        self.tab += 1
        return ROMAN[self.tab] if self.style == "revtex" and self.tab < len(ROMAN) else str(self.tab)


# ---------------------------------------------------------------- display math
def _top_level_rows(body: str) -> list[str]:
    rows, depth, cur, i = [], 0, [], 0
    env_depth = 0
    while i < len(body):
        if body.startswith("\\begin", i):
            env_depth += 1
        elif body.startswith("\\end", i):
            env_depth -= 1
        ch = body[i]
        if ch == "{" and unescaped(body, i):
            depth += 1
        elif ch == "}" and unescaped(body, i):
            depth -= 1
        if body.startswith("\\\\", i) and depth == 0 and env_depth == 0:
            rows.append("".join(cur))
            cur = []
            i += 2
            m = re.match(r"\*?\s*\[[^\]]*\]", body[i:])
            if m:
                i += m.end()
            continue
        cur.append(ch)
        i += 1
    rows.append("".join(cur))
    return [r for r in rows if r.strip()]


TIKZ = re.compile(r"\\begin\s*\{(tikzpicture|tikzcd|fmffile|fmfgraph\*?|feynman|axopicture|picture)\}.*?\\end\s*\{\1\}", re.S)


def _math_refs(m: str) -> str:
    m = TIKZ.sub(r"\\boxed{\\text{diagram (see PDF)}}", m)
    m = re.sub(r"\\includegraphics\w*\s*(?:\[[^\]]*\])?\s*\{[^}]*\}", r"\\boxed{\\text{figure (see PDF)}}", m)
    m = re.sub(r"\\feynmandiagram\s*\[[^\]]*\]\s*\{.*?\};", r"\\boxed{\\text{diagram (see PDF)}}", m, flags=re.S)
    return re.sub(r"\\(eqref|ref|autoref|cref|Cref)\*?\s*\{([^}]*)\}", lambda x: f"\x01{x.group(1)}:{x.group(2)}\x02", m)


def display_math(env: str, body: str, num: Numbering) -> tuple[str, list[str], list[tuple[str, str]], str | None]:
    """Returns (KaTeX-ready LaTeX, equation numbers, [(label, number)], first label)."""
    body = _math_refs(body)
    base = env.rstrip("*")
    starred = env.endswith("*") or base in ("displaymath", "math") or env in ("$$", r"\[")
    labels_all = re.findall(r"\\label\s*\{([^}]*)\}", body)
    numbers: list[str] = []
    lab_nums: list[tuple[str, str]] = []
    if base in ("align", "eqnarray", "gather", "flalign", "alignat") or (base == "dmath" and "\\\\" in body):
        rows = _top_level_rows(body)
        for r in rows:
            tag = re.search(r"\\tag\*?\s*\{([^}]*)\}", r)
            if (starred and not tag) or re.search(r"\\(?:nonumber|notag)\b", r):
                continue
            n = num.next_eq(tag.group(1) if tag else None)
            numbers.append(n)
            for lab in re.findall(r"\\label\s*\{([^}]*)\}", r):
                lab_nums.append((lab, n))
        inner = clean_math(body)
        if base == "eqnarray":
            cols = max((r.count("&") for r in _top_level_rows(inner)), default=2) + 1
            latex = ("\\begin{array}{" + "rcl" + "l" * max(0, cols - 3) + "}\\displaystyle "
                     + re.sub(r"&", r"&\\displaystyle ", inner) + "\\end{array}")
            latex = latex.replace("\\\\", "\\\\\\displaystyle ")
        elif base == "gather":
            latex = "\\begin{gathered}" + inner + "\\end{gathered}"
        elif base == "alignat":
            m = re.match(r"\s*\{(\d+)\}", inner)
            cols = m.group(1) if m else "2"
            latex = "\\begin{alignedat}{" + cols + "}" + (inner[m.end():] if m else inner) + "\\end{alignedat}"
        else:
            latex = "\\begin{aligned}" + inner + "\\end{aligned}"
    else:
        tag = re.search(r"\\tag\*?\s*\{([^}]*)\}", body)
        if (not starred or tag) and not re.search(r"\\(?:nonumber|notag)\b", body):
            n = num.next_eq(tag.group(1) if tag else None)
            numbers.append(n)
            lab_nums += [(lab, n) for lab in labels_all]
        inner = clean_math(body)
        if base == "multline":
            latex = "\\begin{gathered}" + inner + "\\end{gathered}"
        else:
            latex = inner
        latex = re.sub(r"\\begin\{split\}", r"\\begin{aligned}", latex).replace("\\end{split}", "\\end{aligned}")
    latex = re.sub(r"\\begin\{(aligned|gathered|array)\}\s*(?=\\end)", "", latex)
    return latex.strip(), numbers, lab_nums, (labels_all[0] if labels_all else None)


# ---------------------------------------------------------------- bibliography
def parse_bbl(text: str, conv: TextConverter) -> list[dict]:
    text = strip_comments(text)
    m = re.search(r"\\begin\s*\{thebibliography\}\s*\{[^}]*\}", text)
    body = text[m.end():] if m else text
    body = re.split(r"\\end\s*\{thebibliography\}", body)[0]
    refs = []
    items = re.split(r"\\bibitem\s*", body)[1:]
    for it in items:
        j = skip_ws(it, 0)
        if j < len(it) and it[j] == "[":
            _, j = read_group(it, j, "[", "]")
            j = skip_ws(it, j)
        if j < len(it) and it[j] == "{":
            key, j = read_group(it, j)
        else:
            key = f"ref{len(refs) + 1}"
        raw = it[j:]
        raw_ids = re.sub(r"\\(?:href|url)\s*\{([^}]*)\}", r" \1 ", raw)
        arxiv, doi = reference_ids(raw_ids.replace("\\eprint", " arXiv:").replace("\\_", "_"))
        txt = re.sub(r"\\(?:newblock|bibinfo\s*\{[^}]*\}|bibfield\s*\{[^}]*\}|BibitemOpen|BibitemShut\s*\{[^}]*\}|"
                     r"Eprint\s*\{[^}]*\}|selectlanguage\s*\{[^}]*\}|natexlab\s*\{[^}]*\})", " ", raw)
        txt = re.sub(r"\\(?:href|doibase)\s*\{[^}]*\}\s*", "", txt)
        txt = conv.convert(txt)
        txt = re.sub(r"\s+", " ", txt).strip(" .,;")
        refs.append({"key": key.strip(), "n": len(refs) + 1, "text": txt[:600], "arxiv": arxiv, "doi": doi})
    return refs


def _bib_fields(body: str) -> dict[str, str]:
    fields, i, n = {}, 0, len(body)
    while i < n:
        m = re.compile(r"\s*,?\s*([A-Za-z][\w\-]*)\s*=\s*").match(body, i)
        if not m:
            break
        name, j = m.group(1).lower(), m.end()
        if j < n and body[j] == "{":
            val, j = read_group(body, j)
        elif j < n and body[j] == '"':
            k = j + 1
            depth = 0
            while k < n and not (body[k] == '"' and depth == 0):
                depth += (body[k] == "{") - (body[k] == "}")
                k += 1
            val, j = body[j + 1:k], k + 1
        else:
            mm = re.compile(r"[^,}]*").match(body, j)
            val, j = mm.group(0), mm.end()
        fields[name] = re.sub(r"\s+", " ", val.replace("{", "").replace("}", "")).strip()
        i = j
    return fields


def parse_bib(text: str, keys: list[str]) -> list[dict]:
    """Fallback when there is no .bbl: BibTeX entries of the cited keys, in citation order."""
    entries = {}
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", text):
        if m.group(1).lower() in ("comment", "string", "preamble"):
            continue
        body, _ = read_group(text, text.find("{", m.start()))
        entries[m.group(2)] = _bib_fields(body[body.find(",") + 1:])
    refs = []
    for k in keys:
        f = entries.get(k)
        if f is None:
            continue
        authors = [a.strip() for a in f.get("author", "").split(" and ") if a.strip()]
        auth = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
        jr = " ".join(x for x in (f.get("journal"), f.get("volume"), f.get("pages")) if x)
        txt = ". ".join(x for x in (auth, f.get("title"), jr, f"({f['year']})" if f.get("year") else "") if x)
        eprint = f.get("eprint") or (f.get("arxiv") or "").removeprefix("arXiv:") or None
        refs.append({"key": k, "n": len(refs) + 1, "text": accents(txt), "arxiv": eprint or reference_ids(txt)[0],
                     "doi": f.get("doi")})
    return refs


FRONT = re.compile(r"\\(?:author|affiliation|affil|address|institute|email|thanks|date|title|preprint|pacs|keywords|"
                   r"collaboration|altaffiliation|homepage|dedicated|received|abstract|maketitle|noaffiliation|"
                   r"emailAdd|arxivnumber|subheader|abstract|acknowledgments)\*?(?![A-Za-z])")


def _blank(s: str, a: int, b: int) -> str:
    return s[:a] + "".join("\n" if c == "\n" else " " for c in s[a:b]) + s[b:]


def _blank_front_matter(s: str) -> str:
    """Remove title-page commands (authors, affiliations, e-mails, …) keeping every character position and line
    number intact, so source locations stay exact. \\abstract{…} is read separately before this."""
    bd = re.search(r"\\begin\s*\{document\}", s)
    start = bd.end() if bd else 0
    first_sec = re.search(r"\\(?:section|chapter)\*?\s*[\[{]|\\begin\s*\{abstract\}", s[start:])
    stop = start + (first_sec.start() if first_sec else min(len(s) - start, 20000))
    out, i = s, start
    while True:
        m = FRONT.search(out, i, stop)
        if not m:
            break
        _, _, j = read_args(out, m.end(), 1) if m.group(0).lstrip("\\").rstrip("*") not in (
            "maketitle", "noaffiliation", "acknowledgments") else ([], [], m.end())
        out = _blank(out, m.start(), min(j, stop if j > stop else j))
        i = j
    # \\looseness=-1, \\baselineskip=12pt, ... (TeX assignments) anywhere in the body
    for m in list(re.finditer(r"\\[A-Za-z]+\s*=\s*-?[\d.]+\s*(?:pt|em|ex|mm|cm|in|bp)?", out[start:])):
        out = _blank(out, start + m.start(), start + m.end())
    return out


# ---------------------------------------------------------------- main entry
def parse(files: dict[str, str], main: str | None = None) -> dict:
    """`files`: archive path → decoded text (tex, bbl, bib). Raises ValueError if no main file is found."""
    main = main or pick_main(files)
    if not main:
        raise ValueError("no LaTeX main file in the source")
    warnings: list[str] = []
    flat = flatten(files, main, warnings)
    s = _blank_front_matter(flat.text)
    bd = re.search(r"\\begin\s*\{document\}", s)
    pre = s[:bd.start()] if bd else ""
    body_start = bd.end() if bd else 0
    cls = re.search(r"\\documentclass\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}", pre)
    docclass = cls.group(1).strip() if cls else ""
    style = "revtex" if "revtex" in docclass.lower() else ("jhep" if re.search(r"jheppub|JHEP", pre) else "article")
    eq_by_section = style == "jhep" or bool(re.search(r"\\numberwithin\s*\{equation\}\s*\{section\}", pre)) or bool(
        re.search(r"\\renewcommand\s*\{?\\theequation\}?\s*\{\s*\\thesection", pre))
    macros = collect_macros(pre + "\n" + s[body_start:body_start + 20000])
    conv = TextConverter(macros)
    b = DocBuilder("latex", macros=macros, warnings=warnings)

    raw = flat.text  # title/abstract are read before the front matter is blanked
    tm = re.search(r"\\title\s*(?:\[[^\]]*\])?\s*\{", raw)
    if tm:
        t, _ = read_group(raw, tm.end() - 1)
        t = re.sub(r"\\thanks\s*\{.*", "", t, flags=re.S)
        b.title = re.sub(r"\s+", " ", conv.convert(t.replace("\\\\", " "))).strip()
    am = re.search(r"\\begin\s*\{abstract\}(.*?)\\end\s*\{abstract\}", raw, re.S)
    if am:
        b.abstract = conv.convert(am.group(1))
    else:
        am2 = re.search(r"\\abstract\s*\{", raw)
        if am2:
            a, _ = read_group(raw, am2.end() - 1)
            b.abstract = conv.convert(a)

    num = Numbering(style, eq_by_section)
    bib_names: list[str] = []
    refs: list[dict] = []
    pending: list[tuple[int, str]] = []  # (position, text) not yet emitted
    after_eq = False

    def flush():
        nonlocal after_eq
        if not pending:
            return
        start = pending[0][0]
        text = "".join(p for _, p in pending)
        pending.clear()
        offset = 0
        for k, para in enumerate(re.split(r"\n[ \t]*\n", text)):
            md = conv.convert(para)
            pos = start + text.find(para, offset) if para else start
            offset = max(offset, pos - start)
            pos += len(para) - len(para.lstrip())  # the paragraph's first character, not the preceding newline
            blk = b.paragraph(md, flat.where(pos), cont=(k == 0 and after_eq))
            if blk:
                after_eq = False

    pos = body_start
    end_doc = len(s)
    while pos < end_doc:
        m = STRUCT.search(s, pos)
        if not m:
            pending.append((pos, s[pos:]))
            break
        if m.group("dmath") and not unescaped(s, m.start()):
            pending.append((pos, s[pos:m.end()]))
            pos = m.end()
            continue
        pending.append((pos, s[pos:m.start()]))
        here = m.start()
        if m.group("enddoc"):
            break
        if m.group("sec"):
            kind = m.group("sec")
            opts, (title,), j = read_args(s, m.end(), 1)
            if kind in ("paragraph", "subparagraph"):
                pending.append((here, f"\n\n**{title.strip()}.** "))
                pos = j
                continue
            flush()
            label = None
            lm = re.match(r"\s*\\label\s*\{([^}]*)\}", s[j:])
            if lm:
                label, j = lm.group(1), j + lm.end()
            lm2 = re.search(r"\\label\s*\{([^}]*)\}", title)
            label = label or (lm2.group(1) if lm2 else None)
            level = max(1, SECTION_LEVEL.get(kind, 1))
            number, anchor = num.section(level, bool(m.group("star")))
            b.section(conv.convert(title), number, level, anchor, num.appendix, label)
            after_eq = False
            pos = j
            continue
        if m.group("appendix"):
            flush()
            num.appendix = True
            num.sec[1] = 0
            pos = m.end()
            continue
        if m.group("ack"):
            flush()
            b.section("Acknowledgments", None, 1, None, num.appendix)
            pos = m.end()
            continue
        if m.group("endenv"):
            if m.group("endenv") == "subequations":
                num.end_sub()
            pos = m.end()
            continue
        if m.group("bib"):
            names, j = read_group(s, m.end() - 1)
            bib_names += [n.strip() for n in names.split(",") if n.strip()]
            pos = j
            continue
        env = m.group("env") or ("$$" if m.group("dmath") == "$$" else (r"\[" if m.group("dmath") else None))
        if env in ("$$", r"\["):
            close = "$$" if env == "$$" else r"\]"
            k = m.end()
            while True:
                k = s.find(close, k)
                if k < 0 or unescaped(s, k):
                    break
                k += 1
            if k < 0:
                pos = m.end()
                continue
            body, pos = s[m.end():k], k + len(close)
        else:
            endpat = re.compile(r"\\(begin|end)\s*\{" + re.escape(env) + r"\}")
            depth, k = 1, m.end()
            for em in endpat.finditer(s, m.end()):
                depth += 1 if em.group(1) == "begin" else -1
                if depth == 0:
                    k = em.start()
                    pos = em.end()
                    break
            else:
                warnings.append(f"unterminated environment {env}")
                k = pos = len(s)
            body = s[m.end():k]
        if env == "abstract" or env == "keywords":
            continue
        if env == "subequations":
            num.begin_sub()
            pos = m.end()  # parse the inside normally
            continue
        if env in ("acknowledgments", "acknowledgements"):
            flush()
            b.section("Acknowledgments", None, 1, None, num.appendix)
            pending.append((m.end(), body))
            flush()
            continue
        if env == "appendices":
            flush()
            num.appendix = True
            num.sec[1] = 0
            pos = m.end()
            continue
        if env == "thebibliography":
            flush()
            refs = parse_bbl("\\begin{thebibliography}{}" + body + "\\end{thebibliography}", conv)
            continue
        if env in FLOAT_ENVS:
            kind = "table" if "table" in env else "figure"
            cm = re.search(r"\\caption\s*(?:\[[^\]]*\])?\s*\{", body)
            cap = read_group(body, cm.end() - 1)[0] if cm else ""
            lm = re.search(r"\\label\s*\{([^}]*)\}", body)
            if cap or lm:
                flush()
                b.figure(kind, conv.convert(cap), num.next_fig(kind), lm.group(1) if lm else None, flat.where(here))
            continue
        # display math
        flush()
        latex, numbers, lab_nums, label = display_math(env, body, num)
        number = None
        if numbers:
            number = numbers[0] if len(numbers) == 1 else f"{numbers[0]}–{numbers[-1]}"
        if latex:
            b.equation(latex, number, label, env, flat.where(here), lab_nums)
            after_eq = True
    flush()

    # bibliography from a .bbl next to the main file (most arXiv submissions include it), else from .bib
    if not refs:
        stem = main.rsplit(".", 1)[0]
        bbl = files.get(stem + ".bbl") or next((v for k, v in files.items() if k.endswith(".bbl")), None)
        if bbl:
            refs = parse_bbl(bbl, conv)
    if not refs:
        cited = list(dict.fromkeys(k for sec in b.sections for blk in sec["blocks"]
                                   for g in re.findall(r"\[cite:([^\]]*)\]", blk.get("text", ""))
                                   for k in g.split(",")))
        bibs = [v for k, v in files.items() if k.endswith(".bib") and (not bib_names or any(
            k.rsplit("/", 1)[-1].removesuffix(".bib") == n.rsplit("/", 1)[-1].removesuffix(".bib") for n in bib_names))]
        if bibs and cited:
            refs = parse_bib("\n".join(bibs), cited)
            warnings.append("references from .bib (no .bbl): order may differ from the PDF")
    b.references = refs
    return b.build(main_file=main, document_class=docclass, numbering="reconstructed", style=style,
                   files=len(files))
