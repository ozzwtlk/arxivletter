"""Evidence checks for model output (deterministic; nothing here writes new prose).

Rules — the reason the UI can call something "stated by the authors":
  * an author-stated item (open question, limitation, future direction) must carry a quote that appears in the
    paper. The quote is matched after normalisation (whitespace, quotes, dashes, math delimiters, case) against
    the cited block, then against every block (the reference is corrected), then fuzzily (≥ 0.88 similarity of
    the best window, for models that fix a typo). Unverifiable items are dropped and counted, never shown as
    author statements;
  * block / equation references must exist (unknown ids are removed; an equation reference must be an equation);
  * citation keys must exist in the paper's bibliography (models may never introduce outside literature);
  * numbers quoted in results are looked up in the paper; missing ones are flagged (shown as a warning).
Equations shown in a digest are always the paper's own LaTeX, looked up by id; models never write them.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from arxivletter.parsing.document import block_index


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace("“", '"').replace("”", '"').replace("’", "'").replace("‘", "'").replace("–", "-").replace("—", "-")
    s = re.sub(r"\[cite:[^\]]*\]", " ", s)
    s = re.sub(r"[$*_`\\{}]", "", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


class Grounder:
    def __init__(self, doc: dict):
        self.doc = doc
        self.index = block_index(doc)
        self.norm_text = {bid: norm(b.get("text") or b.get("latex") or "") for bid, (_, b) in self.index.items()}
        self.ref_keys = {r["key"] for r in doc.get("references", [])}
        self.all_text = " ".join(self.norm_text.values()) + " " + norm(doc.get("abstract", ""))
        self.stats = {"quotes_ok": 0, "quotes_fixed_ref": 0, "quotes_fuzzy": 0, "dropped_unverified": 0,
                      "refs_removed": 0, "cites_removed": 0, "numbers_flagged": 0}

    # ---------------------------------------------------------------- ids
    def refs(self, ids, kinds: tuple[str, ...] | None = None) -> list[str]:
        out = []
        for i in ids or []:
            i = str(i).strip().strip("[]")
            ok = i in self.index and (kinds is None or self.index[i][1]["type"] in kinds)
            if ok:
                out.append(i)
            elif re.fullmatch(r"S\d+", i) and any(s["id"] == i for s in self.doc["sections"]):
                out.append(i)
            else:
                self.stats["refs_removed"] += 1
        return list(dict.fromkeys(out))

    def equation(self, eid: str) -> dict | None:
        hit = self.index.get(str(eid).strip("[] "))
        return hit[1] if hit and hit[1]["type"] == "equation" else None

    def cites(self, keys) -> list[str]:
        out = []
        for k in keys or []:
            if k in self.ref_keys:
                out.append(k)
            else:
                self.stats["cites_removed"] += 1
        return out

    def section_of(self, bid: str) -> dict | None:
        hit = self.index.get(bid)
        return hit[0] if hit else None

    # ---------------------------------------------------------------- quotes
    def locate(self, quote: str, ref: str | None) -> tuple[str | None, str]:
        """Returns (block id, how) with how ∈ exact | moved | fuzzy | missing."""
        q = norm(quote)
        if len(q) < 12:
            return None, "missing"
        q = q.strip(" .\"'")
        if ref and ref in self.norm_text and q in self.norm_text[ref]:
            return ref, "exact"
        for bid, t in self.norm_text.items():
            if q in t:
                return bid, "moved"
        best, best_id = 0.0, None
        cands = [ref] if ref in self.norm_text else []
        cands += [bid for bid, t in self.norm_text.items() if bid != ref and _shares_words(q, t)]
        for bid in cands[:60]:
            r = _best_window(q, self.norm_text[bid])
            if r > best:
                best, best_id = r, bid
        if best >= 0.88:
            return best_id, "fuzzy"
        return None, "missing"

    def verify_statement(self, item: dict) -> dict | None:
        """Author statement {quote, ref, ...} → item with verified ref + source location, or None."""
        bid, how = self.locate(item.get("quote") or "", (item.get("ref") or "").strip("[] "))
        if not bid:
            self.stats["dropped_unverified"] += 1
            return None
        self.stats[{"exact": "quotes_ok", "moved": "quotes_fixed_ref", "fuzzy": "quotes_fuzzy"}[how]] += 1
        sec, blk = self.index[bid]
        out = dict(item, ref=bid, match=how, section_id=sec["id"], section_number=sec.get("number"),
                   section_title=sec.get("title"), anchor=sec.get("anchor"))
        if how == "fuzzy":
            out["quote"] = _best_window_text(norm(item.get("quote") or ""), blk.get("text") or "")
        if blk.get("src"):
            out["src"] = blk["src"]
        return out

    # ---------------------------------------------------------------- numbers
    NUM = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?(?:\s*(?:\\times|×|x)\s*10\^?\{?-?\d+\}?)?|10\^\{?-?\d+\}?)(?![\w.])")

    def unsupported_numbers(self, text: str) -> list[str]:
        out = []
        for m in self.NUM.finditer(text or ""):
            tok = m.group(1)
            if re.fullmatch(r"\d", tok) or re.fullmatch(r"(19|20)\d\d", tok):
                continue
            t = norm(tok).replace(" ", "")
            digits = re.sub(r"[^\d.\-]", "", t)
            if t not in self.all_text.replace(" ", "") and digits not in self.all_text.replace(" ", ""):
                out.append(tok)
        if out:
            self.stats["numbers_flagged"] += len(out)
        return out


def _shares_words(q: str, t: str) -> bool:
    words = [w for w in q.split() if len(w) > 5][:6]
    return sum(w in t for w in words) >= max(1, len(words) // 2)


def _best_window(q: str, t: str) -> float:
    if not t:
        return 0.0
    n = len(q)
    if len(t) <= n:
        return SequenceMatcher(None, q, t).ratio()
    best = 0.0
    step = max(1, n // 6)
    for i in range(0, len(t) - n + 1, step):
        r = SequenceMatcher(None, q, t[i:i + n]).ratio()
        if r > best:
            best = r
    return best


def _best_window_text(q: str, original: str) -> str:
    """The original (un-normalised) sentence of the block that best matches q."""
    sents = re.split(r"(?<=[.!?])\s+", original)
    return max(sents, key=lambda s: SequenceMatcher(None, q, norm(s)).ratio()) if sents else original
