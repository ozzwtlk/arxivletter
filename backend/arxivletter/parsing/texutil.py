"""Small, dependency-free LaTeX helpers: balanced groups, comments, accents, text-mode → Markdown."""

from __future__ import annotations

import re

ACCENTS = {
    '"': {"a": "ä", "o": "ö", "u": "ü", "A": "Ä", "O": "Ö", "U": "Ü", "e": "ë", "i": "ï", "\\i": "ï", "y": "ÿ"},
    "'": {"a": "á", "e": "é", "i": "í", "\\i": "í", "o": "ó", "u": "ú", "y": "ý", "c": "ć", "n": "ń", "s": "ś",
          "z": "ź", "E": "É", "A": "Á", "O": "Ó", "U": "Ú", "I": "Í", "S": "Ś", "C": "Ć", "Z": "Ź"},
    "`": {"a": "à", "e": "è", "i": "ì", "\\i": "ì", "o": "ò", "u": "ù", "E": "È", "A": "À"},
    "^": {"a": "â", "e": "ê", "i": "î", "\\i": "î", "o": "ô", "u": "û"},
    "~": {"a": "ã", "n": "ñ", "o": "õ", "N": "Ñ"},
    "c": {"c": "ç", "C": "Ç", "s": "ş", "S": "Ş"},
    "v": {"c": "č", "s": "š", "z": "ž", "r": "ř", "e": "ě", "C": "Č", "S": "Š", "Z": "Ž", "R": "Ř", "n": "ň"},
    "H": {"o": "ő", "u": "ű", "O": "Ő", "U": "Ű"},
    "u": {"a": "ă", "g": "ğ"},
    "=": {"a": "ā", "e": "ē", "o": "ō", "u": "ū", "i": "ī"},
    ".": {"z": "ż", "Z": "Ż"},
    "k": {"a": "ą", "e": "ę"},
}
SPECIAL = {r"\ss": "ß", r"\o": "ø", r"\O": "Ø", r"\aa": "å", r"\AA": "Å", r"\ae": "æ", r"\AE": "Æ", r"\l": "ł",
           r"\L": "Ł", r"\i": "ı", r"\oe": "œ"}


def read_group(s: str, i: int, open_: str = "{", close: str = "}") -> tuple[str, int]:
    """s[i] must be `open_`; returns (content, index after the matching close). Escaped braces are skipped.
    Unbalanced input returns the rest of the string."""
    assert s[i] == open_, (s[i:i + 20], open_)
    depth, j = 0, i
    while j < len(s):
        ch = s[j]
        if ch == "\\":
            j += 2
            continue
        if ch == open_:
            depth += 1
        elif ch == close:
            depth -= 1
            if depth == 0:
                return s[i + 1:j], j + 1
        elif open_ == "[" and ch == "{":  # [ ... {]} ... ] — skip brace groups inside optional args
            _, j = read_group(s, j)
            continue
        j += 1
    return s[i + 1:], len(s)


def skip_ws(s: str, i: int) -> int:
    while i < len(s) and s[i] in " \t\n\r":
        i += 1
    return i


def read_args(s: str, i: int, n_req: int, opt: bool = True) -> tuple[list[str | None], list[str], int]:
    """Read [optional] args then `n_req` {required} args (or a single token for an unbraced arg)."""
    opts: list[str | None] = []
    j = skip_ws(s, i)
    while opt and j < len(s) and s[j] == "[":
        o, j = read_group(s, j, "[", "]")
        opts.append(o)
        j = skip_ws(s, j)
    req = []
    for _ in range(n_req):
        j = skip_ws(s, j)
        if j < len(s) and s[j] == "{":
            g, j = read_group(s, j)
        elif j < len(s) and s[j] == "\\":
            m = re.match(r"\\(?:[A-Za-z@]+|.)", s[j:])
            g, j = m.group(0), j + m.end()
        elif j < len(s):
            g, j = s[j], j + 1
        else:
            g = ""
        req.append(g)
    return opts, req, j


def unescaped(s: str, i: int) -> bool:
    """True if s[i] is not preceded by an odd number of backslashes."""
    n, k = 0, i - 1
    while k >= 0 and s[k] == "\\":
        n += 1
        k -= 1
    return n % 2 == 0


def strip_comments(text: str) -> str:
    """Remove % comments (keeping newlines, so line numbers survive), comment environments and \\iffalse blocks."""
    out = []
    for line in text.split("\n"):
        k = 0
        while True:
            k = line.find("%", k)
            if k < 0:
                break
            if unescaped(line, k):
                line = line[:k]
                break
            k += 1
        out.append(line)
    t = "\n".join(out)

    def blank(m: re.Match) -> str:
        return "\n" * m.group(0).count("\n")
    t = re.sub(r"\\begin\{comment\}.*?\\end\{comment\}", blank, t, flags=re.S)
    t = re.sub(r"\\iffalse\b.*?\\fi\b", blank, t, flags=re.S)
    return t


def accents(s: str) -> str:
    def rep(m: re.Match) -> str:
        acc, ch = m.group(1), m.group(2) or m.group(3)
        return ACCENTS.get(acc, {}).get(ch, ch)
    s = re.sub(r"\\([\"'`^~=.])\s*(?:\{(\\?[A-Za-z])\}|(\\i|[A-Za-z]))", rep, s)
    s = re.sub(r"\\([cvHuk])\s*(?:\{(\\?[A-Za-z])\}|\s(\\i|[A-Za-z]))", rep, s)
    s = re.sub(r"\{\\([\"'`^~=.])\s*(\\i|[A-Za-z])\}", lambda m: ACCENTS.get(m.group(1), {}).get(m.group(2), m.group(2)), s)
    for k, v in SPECIAL.items():
        s = re.sub(re.escape(k) + r"(?![A-Za-z])(?:\{\})?", v, s)
    return s


# ---------------------------------------------------------------- inline math split
def split_math(s: str) -> list[tuple[bool, str]]:
    """Split text into (is_math, piece). Recognises $…$, \\(…\\), \\begin{math}…\\end{math}; $$ is display math
    and handled by the block scanner, but a stray $$…$$ here is kept as math too."""
    out: list[tuple[bool, str]] = []
    i, start, n = 0, 0, len(s)
    while i < n:
        ch = s[i]
        if ch == "\\" and i + 1 < n:
            if s[i + 1] == "(":
                end = s.find("\\)", i + 2)
                if end > 0:
                    out.append((False, s[start:i]))
                    out.append((True, s[i + 2:end]))
                    i = start = end + 2
                    continue
            i += 2
            continue
        if ch == "$":
            dbl = s.startswith("$$", i)
            k = i + (2 if dbl else 1)
            while k < n:
                if s[k] == "\\":
                    k += 2
                    continue
                if s[k] == "$":
                    break
                k += 1
            if k < n:
                out.append((False, s[start:i]))
                out.append((True, s[i + (2 if dbl else 1):k]))
                i = start = k + (2 if dbl else 1)
                continue
        i += 1
    out.append((False, s[start:]))
    return [(m, p) for m, p in out if p or m]


MATH_DROP = re.compile(r"\\(?:label|tag\*?)\s*\{[^{}]*\}|\\(?:nonumber|notag|displaybreak(?:\[\d\])?|allowdisplaybreaks|"
                       r"protect|ensuremath)\b|\\xspace\b")


def clean_math(m: str) -> str:
    m = MATH_DROP.sub("", m)
    m = re.sub(r"\\lefteqn\s*\{", "{", m)
    m = re.sub(r"\\(?:hspace|vspace)\*?\s*\{[^{}]*\}", r"\\;", m)
    m = re.sub(r"\n\s*\n+", "\n", m)  # a blank line inside math breaks KaTeX
    return m.strip()
