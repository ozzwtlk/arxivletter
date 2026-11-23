"""Full text of one paper version: LaTeX source first, then arXiv HTML, then PDF, then the abstract.

Every attempt is recorded in the provenance (`attempts`), together with the URL, size and sha256 of what was
parsed, so the reader can always say exactly where a digest's text came from.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import logging
import tarfile
import zipfile

from arxivletter.arxiv import http
from arxivletter.config import settings
from arxivletter.parsing import html as html_parser
from arxivletter.parsing import latex as latex_parser
from arxivletter.parsing import pdf as pdf_parser

log = logging.getLogger("arxivletter.source")

PARSER_VERSION = "latex-4"  # bump when parsing changes block segmentation (digests cite block ids)
TEXT_EXT = (".tex", ".bbl", ".bib", ".ltx", ".latex", ".sty", ".cls", ".def", ".cfg", ".txt")
MIN_BODY_CHARS = 1500  # a parse shorter than this is treated as failed (e.g. a wrapper file around a PDF)


def _decode(b: bytes) -> str:
    for enc in ("utf-8", "latin-1"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", "replace")


def unpack(raw: bytes) -> tuple[str, dict[str, str] | bytes]:
    """e-print payload → ("tex", {path: text}) | ("pdf", bytes) | ("unknown", bytes)."""
    data = raw
    if data[:2] == b"\x1f\x8b":
        try:
            data = gzip.decompress(data)
        except OSError:
            pass
    if data[:4] == b"%PDF":
        return "pdf", data
    if data[:2] == b"PK":
        z = zipfile.ZipFile(io.BytesIO(data))
        return "tex", {n: _decode(z.read(n)) for n in z.namelist() if n.lower().endswith(TEXT_EXT) or "." not in n}
    try:
        tf = tarfile.open(fileobj=io.BytesIO(data))
        files = {}
        for m in tf.getmembers():
            if not m.isfile() or m.size > 20_000_000:
                continue
            name = m.name.lstrip("./")
            if name.lower().endswith(TEXT_EXT) or "." not in name.rsplit("/", 1)[-1]:
                f = tf.extractfile(m)
                if f:
                    files[name] = _decode(f.read())
        return "tex", files
    except tarfile.TarError:
        pass
    head = data[:2000].decode("latin-1", "replace")
    if "\\documentclass" in head or "\\begin{document}" in data.decode("latin-1", "replace"):
        return "tex", {"main.tex": _decode(data)}
    return "unknown", data


def source_text_path(paper_id: str, version: int):
    return settings.paper_dir(paper_id) / f"v{version}.src.json.gz"


def save_source_text(paper_id: str, version: int, files: dict[str, str]) -> None:
    import json
    import os
    if not settings.keep_source_text or os.environ.get("ARXIVLETTER_ROLE") == "worker":
        return  # a worker's disk is not the server's data directory
    keep = {k: v for k, v in files.items() if k.lower().endswith((".tex", ".bbl", ".bib", ".ltx", ".latex")) or "." not in k}
    p = source_text_path(paper_id, version)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(gzip.compress(json.dumps(keep).encode(), 6))


def reparse(paper_id: str, version: int) -> dict | None:
    """Parse the stored LaTeX text again (after a parser upgrade). None if no stored source."""
    import json
    p = source_text_path(paper_id, version)
    if not p.exists():
        return None
    files = json.loads(gzip.decompress(p.read_bytes()))
    doc = latex_parser.parse(files)
    return doc if _ok(doc) else None


def _ok(doc: dict) -> bool:
    return doc["stats"]["chars"] >= MIN_BODY_CHARS and doc["stats"]["paragraphs"] >= 5


def fetch_document(paper_id: str, version: int, title: str = "", abstract: str = "") -> tuple[dict, dict]:
    """Returns (document, provenance). Never raises for content problems; network errors after retries raise."""
    vid = f"{paper_id}v{version}"
    attempts: list[dict] = []
    max_bytes = int(settings.max_source_mb * 1e6)

    def done(doc: dict, kind: str, url: str, body: bytes, extra: dict | None = None) -> tuple[dict, dict]:
        if not doc.get("title"):
            doc["title"] = title
        if not doc.get("abstract"):
            doc["abstract"] = abstract
        prov = {"arxiv_id": vid, "source_kind": kind, "url": url, "bytes": len(body), "parser_version": PARSER_VERSION,
                "sha256": hashlib.sha256(body).hexdigest(), "attempts": attempts, **(extra or {})}
        return doc, prov

    # 1. LaTeX source
    url = settings.eprint_url + vid
    pdf_from_eprint: bytes | None = None
    try:
        r = http.get(url, max_bytes=max_bytes, accept_404=True)
        if r is None:
            attempts.append({"kind": "latex", "url": url, "result": "404"})
        else:
            kind, payload = unpack(r.content)
            if settings.keep_source_archives:
                d = settings.paper_dir(paper_id)
                d.mkdir(parents=True, exist_ok=True)
                (d / f"v{version}.eprint").write_bytes(r.content)
            if kind == "tex":
                save_source_text(paper_id, version, payload)  # type: ignore[arg-type]
                try:
                    doc = latex_parser.parse(payload)  # type: ignore[arg-type]
                    if _ok(doc):
                        attempts.append({"kind": "latex", "url": url, "result": "ok"})
                        return done(doc, "latex", url, r.content, {"main_file": doc["stats"].get("main_file"),
                                                                   "files": sorted(payload)[:200]})
                    attempts.append({"kind": "latex", "url": url, "result": f"too little text ({doc['stats']['chars']} chars)"})
                except Exception as e:  # malformed sources are common; fall back
                    log.warning("%s: LaTeX parse failed: %s", vid, e)
                    attempts.append({"kind": "latex", "url": url, "result": f"parse error: {type(e).__name__}: {e}"[:300]})
            elif kind == "pdf":
                pdf_from_eprint = payload  # type: ignore[assignment]
                attempts.append({"kind": "latex", "url": url, "result": "PDF-only submission"})
            else:
                attempts.append({"kind": "latex", "url": url, "result": "unrecognised archive"})
    except http.FetchError as e:
        if e.status in (413,):
            attempts.append({"kind": "latex", "url": url, "result": str(e)})
        else:
            raise

    # 2. arXiv HTML (LaTeXML)
    url = settings.html_url + vid
    try:
        r = http.get(url, accept_404=True, max_bytes=max_bytes)
        if r is None or "ltx_document" not in r.text[:200000]:
            attempts.append({"kind": "html", "url": url, "result": "not available"})
        else:
            doc = html_parser.parse(r.text)
            if _ok(doc):
                attempts.append({"kind": "html", "url": url, "result": "ok"})
                return done(doc, "html", url, r.content)
            attempts.append({"kind": "html", "url": url, "result": f"too little text ({doc['stats']['chars']} chars)"})
    except http.FetchError as e:
        attempts.append({"kind": "html", "url": url, "result": str(e)[:200]})
    except Exception as e:
        attempts.append({"kind": "html", "url": url, "result": f"parse error: {type(e).__name__}: {e}"[:300]})

    # 3. PDF
    url = settings.pdf_url + vid
    try:
        body = pdf_from_eprint
        if body is None:
            r = http.get(url, accept_404=True, max_bytes=max_bytes)
            body = r.content if r is not None else None
        if body:
            doc = pdf_parser.parse(body)
            if _ok(doc):
                attempts.append({"kind": "pdf", "url": url, "result": "ok"})
                return done(doc, "pdf", url, body)
            attempts.append({"kind": "pdf", "url": url, "result": "too little text"})
        else:
            attempts.append({"kind": "pdf", "url": url, "result": "404"})
    except http.FetchError as e:
        attempts.append({"kind": "pdf", "url": url, "result": str(e)[:200]})
    except Exception as e:
        attempts.append({"kind": "pdf", "url": url, "result": f"parse error: {type(e).__name__}: {e}"[:300]})

    # 4. abstract only
    doc = pdf_parser.abstract_only(title, abstract, "; ".join(f"{a['kind']}: {a['result']}" for a in attempts))
    return done(doc, "abstract", "", abstract.encode())
