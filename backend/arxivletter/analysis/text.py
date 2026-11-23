"""Turning a parsed document into model input: block rendering, chunking, light-mode selection, and the
deterministic detector for sentences in which the authors state open questions, limitations or future work.

Chunking rules (AXIOM's semantic windows, adapted to papers):
  * sections are kept whole when they fit; long sections split only at paragraph boundaries;
  * an equation is never separated from the paragraph before it or from the continuation paragraph after it;
  * acknowledgments and bibliography are skipped; appendices come last and are the first to be dropped when a
    very long paper exceeds the chunk budget (the digest then records what was not read).
Sizes are in characters; MAX_CHUNK_CHARS keeps one chunk + instructions + output inside a 16k-token llama.cpp
slot (LaTeX is ~2.6–3.2 characters per token).
"""

from __future__ import annotations

import re

from arxivletter.config import settings

SKIP_SECTION = re.compile(r"^\s*(acknowledg|references|bibliography|author contributions|data availability|"
                          r"conflicts? of interest|funding)", re.I)
CONCLUSION = re.compile(r"conclu|summary|discussion|outlook|final remarks|future|closing|perspective", re.I)
INTRO = re.compile(r"introduc|motivation|overview", re.I)

# phrases that (almost always) mark an author-stated open problem, limitation or future direction
AUTHOR_STATEMENT = re.compile(
    r"\b(future work|in (?:the )?future|left (?:for|to) (?:future|further)|leave (?:[^.]{0,60}?)(?:for|to) (?:future|"
    r"further|a future|later)|beyond the scope|outside the scope|remains? (?:an )?open|open (?:question|problem|"
    r"issue)s?|unresolved|not (?:yet )?(?:fully )?understood|it would be (?:interesting|worthwhile|important|"
    r"desirable|useful)|would be interesting|worth (?:investigating|exploring|studying)|we plan to|we intend to|"
    r"further (?:study|studies|investigation|work|research) (?:is|are|would be)? ?(?:needed|required|necessary)?|"
    r"deserves? (?:further|more)|warrants? further|a (?:more )?(?:complete|detailed|careful|systematic) "
    r"(?:analysis|treatment|study) (?:is|would be)|caveat|limitation|we (?:have )?neglect(?:ed)?|we (?:do not|did not|"
    r"don't) (?:consider|include|address|account)|has not been (?:considered|studied|addressed)|puzzl(?:e|ing)|"
    r"tension with|remains to be (?:seen|understood|determined|shown|clarified)|an important (?:next|open) step)\b",
    re.I)

MAX_CHUNK_CHARS = 21000
MAX_CHUNKS = 8
LIGHT_CHARS = 21000


def expand_macros(tex: str, macros: dict[str, str], depth: int = 0) -> str:
    """Expand the paper's argument-free macros (\\mchi → m_\\chi) so models read standard LaTeX. Display only:
    stored text keeps the author's macros, and the reader expands them with KaTeX's `macros` option."""
    zero = {k: v for k, v in macros.items() if "#" not in v}
    if not zero or depth > 2:
        return tex
    out = re.sub(r"(\\[A-Za-z@]+)(?![A-Za-z@])", lambda m: "{" + zero[m.group(1)] + "}" if m.group(1) in zero else m.group(0), tex)
    return expand_macros(out, macros, depth + 1) if out != tex else out


def render_block(b: dict, macros: dict | None = None) -> str:
    macros = macros or {}
    if b["type"] == "equation":
        num = f" (Eq. {b['number']})" if b.get("number") else " (unnumbered)"
        return f"[{b['id']}]{num}\n$$ {expand_macros(b['latex'], macros)} $$"
    if b["type"] in ("figure", "table"):
        label = "Figure" if b["type"] == "figure" else "Table"
        return f"[{b['id']}] ({label} {b.get('number') or ''} caption) {expand_macros(b.get('text', ''), macros)}"
    return f"[{b['id']}] {expand_macros(b['text'], macros)}"


def section_header(s: dict) -> str:
    num = f"{s['number']} " if s.get("number") else ""
    return f"## {'Appendix ' if s.get('appendix') and num else ''}{num}{s['title']} [{s['id']}]"


def readable_sections(doc: dict) -> list[dict]:
    return [s for s in doc["sections"] if not SKIP_SECTION.match(s["title"] or "")]


def _units(blocks: list[dict]) -> list[list[dict]]:
    """Group blocks so equations stay with the paragraph before them and the continuation after them."""
    units: list[list[dict]] = []
    for b in blocks:
        if units and (b["type"] == "equation" or b.get("cont")):
            units[-1].append(b)
        else:
            units.append([b])
    return units


def chunk_document(doc: dict, max_chars: int = MAX_CHUNK_CHARS, max_chunks: int = MAX_CHUNKS) -> tuple[list[dict], list[str]]:
    """Returns (chunks, skipped_section_ids). Each chunk: {"text", "sections": [ids], "blocks": [ids]}."""
    macros = doc.get("macros") or {}
    secs = readable_sections(doc)
    main = [s for s in secs if not s.get("appendix")]
    appx = [s for s in secs if s.get("appendix")]
    chunks: list[dict] = []
    cur: dict = {"parts": [], "sections": [], "blocks": [], "chars": 0}

    def close():
        nonlocal cur
        if cur["parts"]:
            chunks.append({"text": "\n\n".join(cur["parts"]), "sections": cur["sections"], "blocks": cur["blocks"]})
        cur = {"parts": [], "sections": [], "blocks": [], "chars": 0}

    for s in main + appx:
        head = section_header(s)
        units = _units(s["blocks"])
        rendered = [[render_block(b, macros) for b in u] for u in units]
        size = len(head) + sum(len(x) + 2 for r in rendered for x in r)
        if cur["chars"] + size > max_chars and cur["parts"]:
            close()
        cur["parts"].append(head)
        cur["sections"].append(s["id"])
        cur["chars"] += len(head)
        for u, r in zip(units, rendered, strict=True):
            usize = sum(len(x) + 2 for x in r)
            if cur["chars"] + usize > max_chars and cur["chars"] > len(head) + 200:
                close()
                cur["parts"].append(head + " (continued)")
                cur["sections"].append(s["id"])
                cur["chars"] += len(head) + 12
            cur["parts"] += [x[:max_chars] for x in r]
            cur["blocks"] += [b["id"] for b in u]
            cur["chars"] += min(usize, max_chars)
    close()
    skipped: list[str] = []
    if len(chunks) > max_chunks:
        # drop appendix-only chunks first, then middle chunks (intro and conclusions are kept)
        appx_ids = {s["id"] for s in appx}
        keep = [c for c in chunks if not set(c["sections"]) <= appx_ids]
        if len(keep) > max_chunks:
            keep = keep[:max_chunks - 2] + keep[-2:]
        skipped = sorted({sid for c in chunks if c not in keep for sid in c["sections"]} -
                         {sid for c in keep for sid in c["sections"]})
        chunks = keep
    return chunks, skipped


def author_statement_blocks(doc: dict) -> list[tuple[dict, dict, list[str]]]:
    """(section, block, matching sentences) for paragraphs in which authors state open problems / limitations."""
    out = []
    for s in readable_sections(doc):
        for b in s["blocks"]:
            if b["type"] != "paragraph":
                continue
            sents = [x for x in re.split(r"(?<=[.!?])\s+(?=[A-Z(\[])", b["text"]) if AUTHOR_STATEMENT.search(x)]
            if sents:
                out.append((s, b, sents))
    return out


def light_selection(doc: dict, budget: int = LIGHT_CHARS) -> tuple[str, list[str]]:
    """Abstract + introduction + conclusions + paragraphs with author statements, for the one-call digest."""
    macros = doc.get("macros") or {}
    secs = readable_sections(doc)
    main = [s for s in secs if not s.get("appendix")]
    picked: dict[str, list[dict]] = {}
    order = {s["id"]: i for i, s in enumerate(doc["sections"])}

    def add(s: dict, blocks: list[dict], cap: int) -> int:
        used = 0
        for b in blocks:
            r = render_block(b, macros)
            if used + len(r) > cap:
                break
            picked.setdefault(s["id"], [])
            if b not in picked[s["id"]]:
                picked[s["id"]].append(b)
                used += len(r)
        return used

    used = 0
    intro = next((s for s in main if INTRO.search(s["title"] or "")), main[0] if main else None)
    if intro:
        used += add(intro, intro["blocks"], int(budget * 0.35))
    concl = [s for s in main if CONCLUSION.search(s["title"] or "")] or main[-1:]
    for s in concl[-2:]:
        used += add(s, s["blocks"], int(budget * 0.35))
    for s, b, _ in author_statement_blocks(doc):
        if used > budget:
            break
        used += add(s, [b], budget - used)
    # key equations: the first few numbered equations of the main text
    if used < budget:
        for s in main:
            for b in s["blocks"]:
                if b["type"] == "equation" and b.get("number") and used < budget * 0.95:
                    used += add(s, [b], budget - used)
            if used > budget * 0.9:
                break
    parts = []
    by_id = {s["id"]: s for s in doc["sections"]}
    for sid in sorted(picked, key=lambda x: order[x]):
        s = by_id[sid]
        bo = {b["id"]: i for i, b in enumerate(s["blocks"])}
        parts.append(section_header(s))
        parts += [render_block(b, macros) for b in sorted(picked[sid], key=lambda b: bo[b["id"]])]
    return "\n\n".join(parts), sorted(picked, key=lambda x: order[x])


def reference_digest(doc: dict, limit: int = 80, width: int = 140) -> str:
    return "\n".join(f"[{r['key']}] {r['text'][:width]}" for r in doc.get("references", [])[:limit])


def approx_tokens(text: str) -> int:
    return int(len(text) / 2.8)


def ctx_budget_chars(max_out_tokens: int) -> int:
    return int((settings.local_ctx_tokens - max_out_tokens - 1800) * 2.8)
