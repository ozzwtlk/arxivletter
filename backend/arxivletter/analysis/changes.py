"""What changed between two versions of a paper (deterministic, no model).

Compared on the parsed documents: abstract, section list, paragraphs (normalised text) and numbered equations.
Shown on revised papers so the reader can decide whether a v2 is worth re-reading.
"""

from __future__ import annotations

from difflib import SequenceMatcher

from arxivletter.analysis.grounding import norm


def _sections(doc: dict) -> dict[str, list[str]]:
    return {(s.get("title") or "").strip().lower(): [norm(b.get("text") or b.get("latex") or "") for b in s["blocks"]]
            for s in doc["sections"]}


def diff(old: dict, new: dict, old_v: int, new_v: int, comments: str | None = None) -> dict:
    so, sn = _sections(old), _sections(new)
    added = [t for t in sn if t not in so]
    removed = [t for t in so if t not in sn]
    changed = []
    for t in sn:
        if t in so:
            a, b = "\n".join(so[t]), "\n".join(sn[t])
            if a != b:
                r = SequenceMatcher(None, a, b, autojunk=False).quick_ratio() if len(a) + len(b) > 40000 else \
                    SequenceMatcher(None, a, b, autojunk=False).ratio()
                if r < 0.995:
                    changed.append({"section": t, "similarity": round(r, 3)})
    eq_old = {b.get("number"): norm(b["latex"]) for s in old["sections"] for b in s["blocks"]
              if b["type"] == "equation" and b.get("number")}
    eq_new = {b.get("number"): norm(b["latex"]) for s in new["sections"] for b in s["blocks"]
              if b["type"] == "equation" and b.get("number")}
    eq_changed = sorted(k for k in eq_new if k in eq_old and eq_new[k] != eq_old[k])
    total_old = sum(len(x) for v in so.values() for x in v) or 1
    total_new = sum(len(x) for v in sn.values() for x in v)
    major = bool(added or removed or len(changed) > 2 or eq_changed or norm(old.get("abstract", "")) != norm(new.get("abstract", "")))
    return {"from_version": old_v, "to_version": new_v, "abstract_changed": norm(old.get("abstract", "")) != norm(new.get("abstract", "")),
            "sections_added": added, "sections_removed": removed, "sections_changed": changed[:30],
            "equations_changed": eq_changed[:40], "equations_added": len(set(eq_new) - set(eq_old)),
            "equations_removed": len(set(eq_old) - set(eq_new)),
            "length_change": round(total_new / total_old - 1, 3), "references_change": len(new.get("references", [])) - len(old.get("references", [])),
            "author_comment": comments, "major": major}
