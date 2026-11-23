"""The parsed-paper model shared by the LaTeX, HTML and PDF parsers.

    {
      "source_kind": "latex" | "html" | "pdf" | "abstract",
      "title", "abstract",
      "macros": {"\\\\mchi": "m_\\\\chi", ...}              # for KaTeX in the reader
      "sections": [{"id": "S3", "number": "3", "title", "level": 1..3, "anchor": "S3.SS1", "appendix": bool,
                    "blocks": [ {"id": "P12", "type": "paragraph", "text": md, "src": {"file", "line"}, "cont": bool},
                                {"id": "E7", "type": "equation", "latex", "number": "3.4", "label", "env", "src"},
                                {"id": "F2", "type": "figure"|"table", "text": caption, "number", "label"} ]}],
      "references": [{"key", "n", "text", "arxiv", "doi"}],
      "labels": {"eq:relic": {"kind": "equation", "id": "E7", "number": "3.4"}, ...},
      "warnings": [...], "stats": {...}
    }

Block ids (P…, E…, F…, T…) are global and stable for a given source, and every model output must cite them; the
verifier (analysis/grounding.py) checks that cited ids exist and that quotes appear verbatim in the cited block.
Paragraph text is Markdown with $…$ math; citations are written `[cite:key1,key2]`, resolved references to labels
as plain numbers (e.g. "Eq. (3.4)").
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

REF_PH = re.compile(r"\x01(eqref|ref|autoref|cref|Cref|pageref):([^\x02]*)\x02")


@dataclass
class DocBuilder:
    source_kind: str
    title: str = ""
    abstract: str = ""
    macros: dict = field(default_factory=dict)
    sections: list = field(default_factory=list)
    references: list = field(default_factory=list)
    labels: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    counts: dict = field(default_factory=lambda: {"P": 0, "E": 0, "F": 0, "T": 0, "S": 0})

    def _id(self, k: str) -> str:
        self.counts[k] += 1
        return f"{k}{self.counts[k]}"

    def section(self, title: str, number: str | None, level: int, anchor: str | None = None,
                appendix: bool = False, label: str | None = None) -> dict:
        sid = self._id("S")
        s = {"id": sid, "number": number, "title": title.strip() or "(untitled)", "level": level,
             "anchor": anchor, "appendix": appendix, "blocks": []}
        self.sections.append(s)
        if label:
            self.labels[label] = {"kind": "section", "id": sid, "number": number}
        return s

    def _cur(self) -> dict:
        if not self.sections:
            self.section("Preamble", None, 1, None)
        return self.sections[-1]

    def paragraph(self, text: str, src: dict | None = None, cont: bool = False) -> dict | None:
        text = text.strip()
        if not text or not re.search(r"[A-Za-z0-9$]", text):
            return None
        b = {"id": self._id("P"), "type": "paragraph", "text": text, "cont": cont}
        if src:
            b["src"] = src
        self._cur()["blocks"].append(b)
        return b

    def equation(self, latex: str, number: str | None, label: str | None = None, env: str = "equation",
                 src: dict | None = None, labels: list[tuple[str, str]] | None = None) -> dict | None:
        if not latex.strip():
            return None
        b = {"id": self._id("E"), "type": "equation", "latex": latex.strip(), "number": number, "label": label,
             "env": env}
        if src:
            b["src"] = src
        self._cur()["blocks"].append(b)
        for lab, num in (labels or ([(label, number)] if label else [])):
            self.labels[lab] = {"kind": "equation", "id": b["id"], "number": num}
        return b

    def figure(self, kind: str, caption: str, number: str | None, label: str | None = None,
               src: dict | None = None) -> dict:
        b = {"id": self._id("F" if kind == "figure" else "T"), "type": kind, "text": caption.strip(), "number": number,
             "label": label}
        if src:
            b["src"] = src
        self._cur()["blocks"].append(b)
        if label:
            self.labels[label] = {"kind": kind, "id": b["id"], "number": number}
        return b

    # ------------------------------------------------------------ finishing
    def resolve_refs(self) -> None:
        unresolved: set[str] = set()

        def rep(m: re.Match) -> str:
            cmd, keys = m.group(1), [k.strip() for k in m.group(2).split(",") if k.strip()]
            outs = []
            for k in keys:
                lab = self.labels.get(k)
                if not lab or not lab.get("number"):
                    unresolved.add(k)
                    outs.append("??")
                    continue
                num = lab["number"]
                if cmd == "eqref":
                    outs.append(f"({num})")
                elif cmd in ("autoref", "cref", "Cref"):
                    word = {"equation": "Eq.", "section": "Sec.", "figure": "Fig.", "table": "Table"}.get(lab["kind"], "")
                    outs.append(f"{word} ({num})" if lab["kind"] == "equation" else f"{word} {num}".strip())
                else:
                    outs.append(str(num))
            return ", ".join(outs)

        def rep_math(m: re.Match) -> str:
            return "\\text{" + rep(m).replace("??", "?") + "}"

        for s in self.sections:
            s["title"] = REF_PH.sub(rep, s["title"])
            for b in s["blocks"]:
                if "text" in b:
                    b["text"] = REF_PH.sub(rep, b["text"])
                if "latex" in b:
                    b["latex"] = REF_PH.sub(rep_math, b["latex"])
        self.abstract = REF_PH.sub(rep, self.abstract)
        if unresolved:
            self.warnings.append(f"{len(unresolved)} unresolved \\ref labels")

    def build(self, **stats) -> dict:
        self.resolve_refs()
        # drop empty sections that have no blocks and no subsections following (keeps structure readable)
        secs = [s for i, s in enumerate(self.sections)
                if s["blocks"] or (i + 1 < len(self.sections) and self.sections[i + 1]["level"] > s["level"])]
        n_par = sum(1 for s in secs for b in s["blocks"] if b["type"] == "paragraph")
        n_eq = sum(1 for s in secs for b in s["blocks"] if b["type"] == "equation")
        chars = sum(len(b.get("text") or b.get("latex") or "") for s in secs for b in s["blocks"])
        return {"source_kind": self.source_kind, "title": self.title, "abstract": self.abstract,
                "macros": self.macros, "sections": secs, "references": self.references, "labels": self.labels,
                "warnings": self.warnings,
                "stats": {"sections": len(secs), "paragraphs": n_par, "equations": n_eq, "chars": chars,
                          "references": len(self.references), **stats}}


def iter_blocks(doc: dict):
    for s in doc["sections"]:
        for b in s["blocks"]:
            yield s, b


def block_index(doc: dict) -> dict[str, tuple[dict, dict]]:
    return {b["id"]: (s, b) for s, b in iter_blocks(doc)}


ARXIV_ID = re.compile(r"(?:arXiv:\s*|arxiv\.org/abs/|\[)?((?:\d{4}\.\d{4,5})|(?:(?:hep|astro|gr|nucl|cond|quant|math|physics)"
                      r"[-\w.]*/\d{7}))(?:v\d+)?", re.I)
DOI = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>{}\\]+[^\s\"<>{}\\.,;])")


def reference_ids(text: str) -> tuple[str | None, str | None]:
    a = ARXIV_ID.search(text)
    d = DOI.search(text)
    return (a.group(1) if a else None), (d.group(1) if d else None)
