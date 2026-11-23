"""Relevance to the reader's research profile, and topic tags for filtering.

Two signals, both explainable:
  * keyword score (deterministic, available immediately after harvesting): weighted whole-word matches of each
    interest's keywords in title (×3) and abstract, combined across interests as 1 − Π(1 − wᵢ·fᵢ);
  * model rating (triage, local model): 0–10 judged against the free-text profile description.
combined = 0.4·keyword + 0.6·model (keyword only until triage ran) + followed-author and category boosts, clamped
to [0, 10]. `relevance_detail` stores every ingredient, and the UI shows it ("why is this ranked here?").
"""

from __future__ import annotations

import json
import math
import re
import tomllib
from dataclasses import dataclass
from functools import lru_cache

from arxivletter.config import settings
from arxivletter.storage import db

PROFILE_KEY = "profile.toml"

# Topic taxonomy used for filters. Deterministic keyword rules; triage may add tags from the same list.
TOPICS: dict[str, list[str]] = {
    "Dark matter": ["dark matter", "/\\bDM\\b/", "WIMP", "relic abundance", "relic density", "freeze-in", "freeze-out",
                    "direct detection", "indirect detection", "dark sector", "hidden sector", "SIDM", "FIMP"],
    "Axions & ALPs": ["axion", "axions", "axion-like", "ALP", "ALPs", "Peccei-Quinn", "haloscope"],
    "Strong fields": ["strong field", "strong-field", "Schwinger", "Euler-Heisenberg", "Heisenberg-Euler",
                      "pair production", "vacuum birefringence", "Furry picture", "Volkov", "intense laser",
                      "high-intensity laser", "worldline", "magnetar", "nonlinear Compton", "Breit-Wheeler",
                      "background field", "external field", "critical field"],
    "Neutrinos": ["neutrino", "neutrinos", "seesaw", "leptogenesis", "heavy neutral lepton"],
    "Collider & BSM": ["LHC", "collider", "ATLAS", "CMS", "beyond the Standard Model", "BSM", "new physics",
                       "long-lived particle", "FCC", "muon collider", "SMEFT"],
    "Higgs & electroweak": ["Higgs", "electroweak", "W boson", "Z boson", "top quark"],
    "Flavor & precision": ["flavor", "flavour", "B meson", "B decays", "CKM", "lepton flavor", "g-2", "EDM",
                           "electric dipole moment", "kaon"],
    "QCD & hadrons": ["QCD", "hadron", "hadrons", "quarkonium", "parton", "PDF", "jet", "jets", "gluon",
                      "heavy-ion", "quark-gluon plasma", "chiral perturbation theory", "nucleon"],
    "Lattice": ["lattice"],
    "Cosmology & early universe": ["inflation", "cosmological", "cosmology", "early universe", "baryogenesis",
                                   "reheating", "BBN", "CMB", "phase transition", "dark energy"],
    "Gravitational waves": ["gravitational wave", "gravitational waves", "LIGO", "LISA", "pulsar timing"],
    "Amplitudes": ["scattering amplitude", "scattering amplitudes", "on-shell", "S-matrix", "bootstrap",
                   "positivity bounds", "double copy"],
    "Holography & AdS/CFT": ["holography", "holographic", "AdS/CFT", "AdS", "entanglement entropy", "SYK"],
    "String theory": ["string theory", "string", "strings", "brane", "branes", "Calabi-Yau", "swampland",
                      "compactification", "M-theory", "F-theory"],
    "Formal QFT": ["conformal field theory", "CFT", "renormalization", "anomaly", "anomalies", "supersymmetric",
                   "supersymmetry", "gauge theory", "generalized symmetries", "resurgence", "instanton",
                   "topological", "effective action", "Schwinger-Keldysh"],
    "Black holes & gravity": ["black hole", "black holes", "quantum gravity", "de Sitter", "general relativity",
                              "Hawking"],
    "EFT": ["effective field theory", "EFT", "SMEFT", "matching", "Wilson coefficients"],
    "Astroparticle": ["cosmic ray", "cosmic rays", "gamma-ray", "IceCube", "neutron star", "neutron stars",
                      "supernova", "magnetar", "white dwarf"],
}


@dataclass(frozen=True)
class Interest:
    name: str
    weight: float
    patterns: tuple[re.Pattern, ...]
    keywords: tuple[str, ...]


@dataclass(frozen=True)
class Profile:
    description: str
    interests: tuple[Interest, ...]
    follow: tuple[str, ...]
    category_boost: dict
    raw: str


def _compile(kw: str) -> re.Pattern:
    if len(kw) > 2 and kw.startswith("/") and kw.endswith("/"):
        return re.compile(kw[1:-1])
    esc = re.escape(kw).replace(r"\-", "[-\\s]?").replace(r"\ ", r"[\s\-]+")
    return re.compile(r"(?<![A-Za-z0-9])" + esc + r"(?![A-Za-z0-9])", re.I if not kw.isupper() else 0)


def parse_profile(text: str) -> Profile:
    d = tomllib.loads(text)
    ints = []
    for it in d.get("interests", []):
        kws = tuple(str(k) for k in it.get("keywords", []))
        ints.append(Interest(str(it.get("name", "interest")), float(it.get("weight", 1.0)),
                             tuple(_compile(k) for k in kws), kws))
    return Profile(str(d.get("reader", {}).get("description", "")).strip(), tuple(ints),
                   tuple(str(a) for a in d.get("authors", {}).get("follow", [])),
                   dict(d.get("categories", {}).get("boost", {})), text)


def profile_text() -> str:
    try:
        row = db.q1("SELECT value FROM meta WHERE key=?", (PROFILE_KEY,))
    except Exception:
        row = None
    if row and row["value"]:
        return row["value"]
    p = settings.profile_path
    return p.read_text() if p.exists() else '[reader]\ndescription = ""\n'


@lru_cache(maxsize=4)
def _parsed(text: str) -> Profile:
    return parse_profile(text)


def profile() -> Profile:
    return _parsed(profile_text())


def save_profile(text: str) -> Profile:
    p = parse_profile(text)  # raises on invalid TOML
    with db.connect() as c:
        c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?,?)", (PROFILE_KEY, text))
    return p


_TOPIC_RE = {t: [_compile(k) for k in kws] for t, kws in TOPICS.items()}


def topics_for(title: str, abstract: str) -> list[str]:
    text = f"{title}\n{abstract}"
    out = []
    for t, pats in _TOPIC_RE.items():
        n = sum(len(p.findall(text)) for p in pats)
        if n >= (1 if any(p.search(title) for p in pats) else 2) or (t in ("Lattice",) and n):
            out.append(t)
    return out


def _author_key(name: str) -> str:
    parts = re.sub(r"[^\w\s\-]", " ", name).split()
    return (parts[-1] + " " + parts[0][0]).lower() if parts else ""


def keyword_score(title: str, abstract: str, prof: Profile | None = None) -> tuple[float, list[dict]]:
    prof = prof or profile()
    detail, keep = [], 1.0
    for it in prof.interests:
        hits, matched = 0.0, []
        for kw, p in zip(it.keywords, it.patterns, strict=True):
            nt, na = len(p.findall(title)), len(p.findall(abstract))
            if nt or na:
                hits += 3 * min(nt, 2) + min(na, 3)
                matched.append(re.sub(r"\\[bB]|[()?:]", "", kw.strip("/")))
        if hits:
            f = 0.92 * (1 - math.exp(-hits / 7))  # saturates slowly: 3 title hits ≈ 0.66, 12 hits ≈ 0.75
            keep *= 1 - it.weight * f
            detail.append({"interest": it.name, "score": round(10 * it.weight * f, 2), "matched": matched[:8]})
    detail.sort(key=lambda x: -x["score"])
    return round(10 * (1 - keep), 2), detail


def combine(paper: dict, llm_rating: float | None = None, prof: Profile | None = None) -> tuple[float, dict]:
    prof = prof or profile()
    kw, detail = keyword_score(paper["title"], paper.get("abstract") or "", prof)
    base = kw if llm_rating is None else 0.4 * kw + 0.6 * float(llm_rating)
    boost = float(prof.category_boost.get(paper.get("primary_category") or "", 0))
    follow = {_author_key(a) for a in prof.follow}
    by = [a for a in paper.get("authors", []) if _author_key(a) in follow]
    if by:
        boost += 1.5
    score = max(0.0, min(10.0, base + boost))
    return round(score, 2), {"keyword": kw, "model": llm_rating, "boost": boost, "followed_authors": by,
                             "interests": detail}


def rescore_all() -> int:
    """After the profile changed: recompute every paper's combined relevance (model ratings are kept)."""
    prof = profile()
    n = 0
    rows = db.q("SELECT id, title, abstract, authors, primary_category, relevance_detail FROM papers")
    with db.connect() as c:
        for r in rows:
            d = json.loads(r["relevance_detail"] or "{}")
            p = {"title": r["title"], "abstract": r["abstract"], "authors": json.loads(r["authors"]),
                 "primary_category": r["primary_category"]}
            s, det = combine(p, d.get("model"), prof)
            if d.get("model_reason"):
                det["model_reason"] = d["model_reason"]
            c.execute("UPDATE papers SET relevance=?, relevance_detail=? WHERE id=?", (s, json.dumps(det), r["id"]))
            n += 1
    return n
