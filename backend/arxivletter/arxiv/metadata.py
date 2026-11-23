"""Paper metadata from arXiv's supported interfaces.

* OAI-PMH, metadataPrefix=arXivRaw (bulk/incremental harvesting): one record per paper with *every* version and its
  exact submission timestamp. Records appear when a paper is announced (new or replaced, also cross-lists); the
  datestamp is the announcement/change date, so harvesting by datestamp is naturally incremental and supports
  backfilling any date range.
* arXiv API (Atom, id_list): metadata for individual papers, e.g. a paper added by hand.

Both produce the same `Meta` dict, which `catalog.upsert` deduplicates by arXiv id and version.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Iterator

from arxivletter.arxiv import http
from arxivletter.config import settings

log = logging.getLogger("arxivletter.metadata")

OAI = "{http://www.openarchives.org/OAI/2.0/}"
RAW = "{http://arxiv.org/OAI/arXivRaw/}"
ATOM = "{http://www.w3.org/2005/Atom}"
AX = "{http://arxiv.org/schemas/atom}"

ID_RE = re.compile(r"^(?:arXiv:)?((?:\d{4}\.\d{4,5})|(?:[a-z\-]+(?:\.[A-Z]{2})?/\d{7}))(?:v(\d+))?$", re.I)


def parse_id(s: str) -> tuple[str, int | None]:
    """'2611.12345v2' → ('2611.12345', 2); 'hep-ph/0601001' → ('hep-ph/0601001', None)."""
    s = s.strip().rstrip("/")
    s = re.sub(r"^https?://(?:export\.)?arxiv\.org/(?:abs|pdf|html)/", "", s).removesuffix(".pdf")
    m = ID_RE.match(s)
    if not m:
        raise ValueError(f"not an arXiv identifier: {s!r}")
    return m.group(1), (int(m.group(2)) if m.group(2) else None)


def split_authors(raw: str) -> list[str]:
    """arXivRaw gives 'A. One, B. Two (Uni X) and C. Three'. Drop affiliations in parentheses, split on , / and."""
    depth, out = 0, []
    for ch in raw:
        depth += ch == "("
        if depth == 0:
            out.append(ch)
        depth -= ch == ")" and depth > 0
    s = re.sub(r"\s+", " ", "".join(out)).strip()
    parts = re.split(r",\s*(?:and\s+)?|\s+and\s+", s)
    return [p.strip(" ,;") for p in parts if p.strip(" ,;") and p.strip().lower() not in ("et al", "et al.")]


def _clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _text_accents(s: str) -> str:
    """LaTeX accents (G\\"odel, Gonz\\'alez) → Unicode, outside math only."""
    from arxivletter.parsing.texutil import accents, split_math
    if "\\" not in s:
        return s
    return "".join(f"${p}$" if m else accents(p) for m, p in split_math(s))


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _text(el, tag: str) -> str | None:
    x = el.find(tag)
    return _clean(x.text) if x is not None and x.text else None


def parse_raw_record(rec) -> dict | None:
    head = rec.find(OAI + "header")
    if head is not None and head.get("status") == "deleted":
        return None
    meta = rec.find(f"{OAI}metadata/{RAW}arXivRaw")
    if meta is None:
        return None
    versions = []
    for v in meta.findall(RAW + "version"):
        try:
            dt = parsedate_to_datetime(_text(v, RAW + "date") or "")
        except (TypeError, ValueError):
            continue
        versions.append({"v": int((v.get("version") or "v1").lstrip("v")), "date": _iso(dt),
                         "size": _text(v, RAW + "size"), "source_type": _text(v, RAW + "source_type")})
    versions.sort(key=lambda x: x["v"])
    cats = (_text(meta, RAW + "categories") or "").split()
    return {
        "id": _text(meta, RAW + "id"), "title": _text_accents(_text(meta, RAW + "title") or "(untitled)"),
        "authors": [_text_accents(a) for a in split_authors(_text(meta, RAW + "authors") or "")],
        "abstract": _text_accents(_text(meta, RAW + "abstract") or ""),
        "categories": cats, "primary_category": cats[0] if cats else None,
        "comments": _text(meta, RAW + "comments"), "journal_ref": _text(meta, RAW + "journal-ref"),
        "doi": _text(meta, RAW + "doi"), "license": _text(meta, RAW + "license"),
        "report_no": _text(meta, RAW + "report-no"), "versions": versions,
        "oai_datestamp": _text(head, OAI + "datestamp") if head is not None else None, "source_meta": "oai",
    }


def oai_records(set_spec: str, from_day: str, until_day: str) -> Iterator[dict]:
    """All arXivRaw records of an OAI set (e.g. 'physics:hep-ph') with datestamps in [from_day, until_day]."""
    params: dict = {"verb": "ListRecords", "metadataPrefix": "arXivRaw", "set": set_spec,
                    "from": from_day, "until": until_day}
    while True:
        r = http.get(settings.oai_url, params=params)
        root = ET.fromstring(r.content)
        err = root.find(OAI + "error")
        if err is not None:
            if err.get("code") == "noRecordsMatch":
                return
            raise http.FetchError(f"OAI-PMH error {err.get('code')}: {_clean(err.text)}")
        lr = root.find(OAI + "ListRecords")
        if lr is None:
            return
        for rec in lr.findall(OAI + "record"):
            d = parse_raw_record(rec)
            if d and d["id"] and d["versions"]:
                yield d
        tok = lr.find(OAI + "resumptionToken")
        if tok is None or not (tok.text or "").strip():
            return
        params = {"verb": "ListRecords", "resumptionToken": tok.text.strip()}


def set_spec(category: str) -> str:
    """OAI set for an arXiv category: hep-ph → physics:hep-ph, math.MP → math:math:MP (archive-level sets)."""
    if "." in category:
        archive = category.split(".")[0]
        group = "physics" if archive in ("astro-ph", "cond-mat", "physics", "nlin", "quant-ph") else archive
        return f"{group}:{archive}:{category.split('.', 1)[1]}" if group != archive else f"{archive}:{category}"
    return f"physics:{category}"


def api_lookup(ids: list[str]) -> list[dict]:
    """Metadata via the arXiv API (Atom). The API reports only the latest version's date (`updated`) and v1's
    (`published`); intermediate version dates stay unknown until the next OAI harvest fills them in."""
    if not ids:
        return []
    r = http.get(settings.api_url, params={"id_list": ",".join(ids), "max_results": len(ids)})
    root = ET.fromstring(r.content)
    out = []
    for e in root.findall(ATOM + "entry"):
        raw_id = (e.findtext(ATOM + "id") or "").rsplit("/abs/", 1)[-1]
        if not raw_id or e.findtext(ATOM + "title") in (None, "Error"):
            continue
        pid, ver = parse_id(raw_id)
        published = e.findtext(ATOM + "published")
        updated = e.findtext(ATOM + "updated")
        versions = [{"v": 1, "date": published, "size": None, "source_type": None}]
        if ver and ver > 1:
            versions.append({"v": ver, "date": updated, "size": None, "source_type": None})
        cats = [c.get("term") for c in e.findall(ATOM + "category") if c.get("term")]
        prim = e.find(AX + "primary_category")
        primary = prim.get("term") if prim is not None else (cats[0] if cats else None)
        out.append({
            "id": pid, "title": _text_accents(_clean(e.findtext(ATOM + "title"))),
            "abstract": _text_accents(_clean(e.findtext(ATOM + "summary"))),
            "authors": [_text_accents(_clean(a.findtext(ATOM + "name"))) for a in e.findall(ATOM + "author")],
            "categories": [primary] + [c for c in cats if c != primary] if primary else cats,
            "primary_category": primary, "comments": _clean(e.findtext(AX + "comment")) or None,
            "journal_ref": _clean(e.findtext(AX + "journal_ref")) or None, "doi": _clean(e.findtext(AX + "doi")) or None,
            "license": None, "report_no": None, "versions": versions, "oai_datestamp": None, "source_meta": "api",
        })
    return out


def api_search_day(categories: tuple[str, ...], day: str, field: str = "submittedDate", page: int = 500) -> list[dict]:
    """Papers of the given categories whose v1 (submittedDate) or latest version (lastUpdatedDate) falls on `day`
    (UTC). Used for backfilling by submission date when OAI datestamps are inconvenient."""
    d = day.replace("-", "")
    q = "(" + " OR ".join(f"cat:{c}" for c in categories) + f") AND {field}:[{d}0000 TO {d}2359]"
    out, start = [], 0
    while True:
        r = http.get(settings.api_url, params={"search_query": q, "start": start, "max_results": page,
                                               "sortBy": "submittedDate", "sortOrder": "ascending"})
        root = ET.fromstring(r.content)
        ids = [(e.findtext(ATOM + "id") or "").rsplit("/abs/", 1)[-1] for e in root.findall(ATOM + "entry")]
        ids = [i for i in ids if i]
        if not ids:
            break
        out += ids
        total = int(root.findtext("{http://a9.com/-/spec/opensearch/1.1/}totalResults") or 0)
        start += len(ids)
        if start >= total:
            break
    return out
