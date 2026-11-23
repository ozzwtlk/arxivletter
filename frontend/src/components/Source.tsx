import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "../api/client";
import type { Block, PaperDocument, Reference, Section } from "../api/types";
import { setPaperContext } from "../lib/markdown";
import { Icon } from "./Icon";
import { Equation, Md } from "./Md";

interface SourceCtx {
  doc: PaperDocument | null;
  loading: boolean;
  ensure: () => void;
  block: (id: string) => { section: Section; block: Block } | null;
  section: (id: string) => Section | null;
  open: (id: string) => void;
  openCites: (keys: string[]) => void;
  htmlUrl: string;
}

const Ctx = createContext<SourceCtx | null>(null);
export const useSource = () => useContext(Ctx)!;

/** Loads the parsed full text once per paper; provides lookups and the source drawer. */
export function SourceProvider({ paperId, version, htmlUrl, children }: { paperId: string; version?: number; htmlUrl: string; children: ReactNode }) {
  const [doc, setDoc] = useState<PaperDocument | null>(null);
  const [loading, setLoading] = useState(false);
  const [wanted, setWanted] = useState(true);
  const [drawer, setDrawer] = useState<{ kind: "block"; id: string } | { kind: "cites"; keys: string[] } | null>(null);

  useEffect(() => {
    setDoc(null);
    setPaperContext(paperId + "-pending", {}, {});
  }, [paperId]);
  useEffect(() => {
    if (!wanted || doc) return;
    let alive = true;
    setLoading(true);
    api.content(paperId, version).then((c) => {
      if (!alive) return;
      const cites: Record<string, number> = {};
      c.document.references.forEach((r) => (cites[r.key] = r.n));
      setPaperContext(`${paperId}v${c.version}`, c.document.macros, cites);
      setDoc(c.document);
    }).catch(() => {}).finally(() => alive && setLoading(false));
    return () => { alive = false; };
  }, [paperId, version, wanted, doc]);

  const index = useMemo(() => {
    const m = new Map<string, { section: Section; block: Block }>();
    doc?.sections.forEach((s) => s.blocks.forEach((b) => m.set(b.id, { section: s, block: b })));
    return m;
  }, [doc]);
  const block = useCallback((id: string) => index.get(id) ?? null, [index]);
  const section = useCallback((id: string) => doc?.sections.find((s) => s.id === id) ?? null, [doc]);
  const value: SourceCtx = {
    doc, loading, ensure: () => setWanted(true), block, section,
    open: (id) => setDrawer({ kind: "block", id }), openCites: (keys) => setDrawer({ kind: "cites", keys }), htmlUrl,
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setDrawer(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <Ctx.Provider value={value}>
      <div
        onClick={(e) => {
          const br = (e.target as HTMLElement).closest("a.blockref") as HTMLAnchorElement | null;
          if (br) {
            e.preventDefault();
            const ref = br.dataset.ref ?? "";
            if (/^Q\d+$/.test(ref)) jumpTo("oq-" + ref);
            else setDrawer({ kind: "block", id: ref });
            return;
          }
          const a = (e.target as HTMLElement).closest("a.cite") as HTMLAnchorElement | null;
          if (a) {
            e.preventDefault();
            setDrawer({ kind: "cites", keys: (a.dataset.keys ?? "").split(",").filter(Boolean) });
          }
        }}
      >
        {children}
      </div>
      {drawer && (
        <div className="drawer-wrap" onClick={() => setDrawer(null)}>
          <aside className="drawer card" onClick={(e) => e.stopPropagation()} aria-label="Source">
            <button type="button" className="icon-btn drawer-close" onClick={() => setDrawer(null)} aria-label="Close"><Icon name="x" /></button>
            {drawer.kind === "block" ? <BlockView id={drawer.id} onClose={() => setDrawer(null)} /> : <CitesView keys={drawer.keys} />}
          </aside>
        </div>
      )}
    </Ctx.Provider>
  );
}

export function sectionLabel(s: Section | null | undefined): string {
  if (!s) return "";
  return `${s.number ? (s.appendix ? `App. ${s.number}` : `§${s.number}`) + " " : ""}${s.title}`;
}

function BlockView({ id, onClose }: { id: string; onClose: () => void }) {
  const src = useSource();
  const hit = src.block(id);
  if (src.loading || !src.doc) return <p className="muted">Loading the full text…</p>;
  if (!hit) {
    const s = src.section(id);
    if (s) return <><span className="eyebrow">Section</span><h3>{sectionLabel(s)}</h3><button type="button" className="btn btn-quiet btn-sm" onClick={() => { onClose(); jumpTo(s.id); }}>Show in full text</button></>;
    return <p className="muted">Block {id} not found in this version.</p>;
  }
  const { section, block } = hit;
  return (
    <div className="block-view">
      <span className="eyebrow">Source · {block.type === "equation" ? `Eq. ${block.number ? `(${block.number})` : "(unnumbered)"}` : block.type === "paragraph" ? "paragraph" : block.type} · {block.id}</span>
      <h3>{sectionLabel(section)}</h3>
      <div className="block-body">
        {block.type === "equation" ? <Equation latex={block.latex ?? ""} number={block.number} /> : <Md src={(block.type !== "paragraph" ? `**${block.type === "figure" ? "Figure" : "Table"} ${block.number ?? ""}.** ` : "") + (block.text ?? "")} />}
      </div>
      <div className="block-loc small muted">
        {block.src && <span title="Location in the author's source">{block.src.file === "pdf" ? `PDF page ${block.src.line}` : `${block.src.file}:${block.src.line}`}</span>}
        {section.anchor && <a href={`${src.htmlUrl}#${section.anchor}`} target="_blank" rel="noreferrer">arXiv HTML {section.anchor} <Icon name="external" /></a>}
        <button type="button" className="link-btn" onClick={() => { onClose(); jumpTo(block.id); }}>Show in full text</button>
      </div>
    </div>
  );
}

function refLinks(r: Reference) {
  return (
    <span className="ref-links">
      {r.arxiv && <a href={`https://arxiv.org/abs/${r.arxiv}`} target="_blank" rel="noreferrer">arXiv:{r.arxiv}</a>}
      {r.doi && <a href={`https://doi.org/${r.doi}`} target="_blank" rel="noreferrer">doi</a>}
      {!r.arxiv && !r.doi && <a href={`https://inspirehep.net/search?p=${encodeURIComponent(r.text.slice(0, 120))}`} target="_blank" rel="noreferrer">INSPIRE search</a>}
    </span>
  );
}

function CitesView({ keys }: { keys: string[] }) {
  const src = useSource();
  const refs = src.doc?.references ?? [];
  const found = keys.map((k) => refs.find((r) => r.key === k)).filter(Boolean) as Reference[];
  return (
    <div>
      <span className="eyebrow">Cited references</span>
      {found.length === 0 && <p className="muted">Not in the parsed bibliography ({keys.join(", ")}).</p>}
      <ol className="ref-list">
        {found.map((r) => (
          <li key={r.key} value={r.n}><Md src={r.text} /> {refLinks(r)}</li>
        ))}
      </ol>
    </div>
  );
}

export function ReferenceList({ refs }: { refs: Reference[] }) {
  return (
    <ol className="ref-list">
      {refs.map((r) => (
        <li key={r.key} id={`ref-${r.key}`} value={r.n}><Md src={r.text} /> {refLinks(r)}</li>
      ))}
    </ol>
  );
}

export function jumpTo(id: string) {
  const el = document.getElementById("blk-" + id) ?? document.getElementById("sec-" + id);
  if (el) {
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    el.classList.add("flash");
    window.setTimeout(() => el.classList.remove("flash"), 2200);
  } else {
    window.dispatchEvent(new CustomEvent("axl-open-fulltext", { detail: id }));
  }
}

/** Clickable source chips: "§3 · P12", "Eq. (2.4)". */
export function Refs({ ids }: { ids: string[] | undefined }) {
  const src = useSource();
  if (!ids?.length) return null;
  return (
    <span className="refs">
      {ids.map((id) => {
        const hit = src.block(id);
        let label = id;
        if (hit?.block.type === "equation") label = hit.block.number ? `Eq. (${hit.block.number})` : "Eq.";
        else if (hit) label = hit.section.number ? `${hit.section.appendix ? "App. " : "§"}${hit.section.number}` : hit.section.title.slice(0, 18);
        else if (/^Q\d+$/.test(id)) label = id;
        return (
          <button key={id} type="button" className="ref-chip" onClick={() => (/^Q\d+$/.test(id) ? jumpTo("oq-" + id) : src.open(id))} title={`Show source ${id}`}>
            {label}
          </button>
        );
      })}
    </span>
  );
}
