import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { AuthorStatement, Changes, Digest, Gap, GapsAnalysis, Idea, PaperDetail, Refd } from "../api/types";
import { BookmarkButton } from "../components/Bookmarks";
import { Icon } from "../components/Icon";
import { Equation, Md, MdInline } from "../components/Md";
import { ModelPicker } from "../components/ModelPicker";
import { Authors, RelevanceBadge, StatusTag } from "../components/PaperCard";
import { jumpTo, ReferenceList, Refs, sectionLabel, SourceProvider, useSource } from "../components/Source";
import { ago, fmtDay, KIND_LABEL, useLoad } from "../lib/hooks";

const pid = (p: string | undefined) => p ?? "";

export function PaperPage() {
  const params = useParams();
  const id = pid(params["*"]);
  const { data: p, error, reload } = useLoad(() => api.paper(id), [id], (d) =>
    d.jobs.some((j) => j.status === "pending" || j.status === "running") ? 8000 : false);
  useEffect(() => { window.scrollTo(0, 0); }, [id]);
  if (error) return <main className="page"><p className="error">{error}</p></main>;
  if (!p) return <main className="page"><p className="muted">Loading…</p></main>;
  return (
    <SourceProvider paperId={p.id} version={p.content?.version} htmlUrl={`https://arxiv.org/html/${p.id}v${p.content?.version ?? p.version}`}>
      <PaperBody p={p} reload={reload} />
    </SourceProvider>
  );
}

function List({ items }: { items: Refd[] }) {
  if (!items?.length) return <p className="muted small">None stated.</p>;
  return (
    <ul className="refd">
      {items.map((x, i) => (
        <li key={i}>
          <MdInline src={x.text} /> <Refs ids={x.refs} />
          {x.unsupported_numbers?.length ? (
            <span className="warn-num" title="These numbers were not found verbatim in the paper text; check them against the source.">
              ⚠ unverified: {x.unsupported_numbers.join(", ")}
            </span>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

function Meter({ label, value, max = 5 }: { label: string; value: number; max?: number }) {
  return (
    <span className="meter" title={`${label}: ${value}/${max}`}>
      <span className="meter-label">{label}</span>
      {Array.from({ length: max }, (_, i) => <span key={i} className={i < value ? "on" : ""} />)}
    </span>
  );
}

function AuthorQuestion({ q, p }: { q: AuthorStatement; p: PaperDetail }) {
  const src = useSource();
  return (
    <li className={`oq imp-${q.importance ?? 1}`} id={`blk-oq-${q.id}`}>
      <div className="oq-head">
        <span className={`kind-badge k-${q.kind}`}>{KIND_LABEL[q.kind] ?? q.kind}</span>
        {q.importance === 3 && <span className="tag tag-key">central</span>}
        <span className="spacer" />
        <button type="button" className="ref-chip loc" onClick={() => src.open(q.ref)} title="Open the source paragraph">
          {q.section_number ? `§${q.section_number}` : ""} {q.section_title?.slice(0, 40)}
        </button>
        {q.also_at?.map((r) => <Refs key={r} ids={[r]} />)}
        <BookmarkButton paper={p} kind="question" title={q.summary || "Author statement"} text={q.quote} identity={q.quote} />
      </div>
      {q.summary && <p className="oq-summary"><MdInline src={q.summary} /></p>}
      <blockquote className="quote" title={q.match === "fuzzy" ? "Matched approximately (≥ 88 %) to the source" : "Verbatim from the paper"}>
        <Md src={q.quote} />
        <span className="quote-tag">{q.match === "detected" ? "detected in text" : q.match === "fuzzy" ? "≈ verbatim" : "verbatim"}{q.src && q.src.file !== "pdf" ? ` · ${q.src.file}:${q.src.line}` : ""}</span>
      </blockquote>
      {q.context && <p className="oq-context"><span className="ai-label">AI note</span> <MdInline src={q.context} /></p>}
    </li>
  );
}

function GapCard({ g, n, p }: { g: Gap; n: number; p: PaperDetail }) {
  const src = useSource();
  return (
    <article className={`gap card ${g.ungrounded ? "ungrounded" : ""}`}>
      <div className="gap-head">
        <span className="gap-n">{n}</span>
        <div>
          <span className={`kind-badge k-ai`}>{KIND_LABEL[g.kind] ?? g.kind}</span>
          <h4><MdInline src={g.title} /></h4>
        </div>
      </div>
      <BookmarkButton paper={p} kind="gap" title={g.title} text={g.description} identity={g.description} />
      <div className="gap-meters">
        <Meter label="impact" value={g.impact} />
        <Meter label="difficulty" value={g.difficulty} />
        <span className={`conf conf-${g.confidence}`}>{g.confidence} confidence</span>
        {g.ungrounded && <span className="tag tag-warn" title="None of the cited passages could be verified">ungrounded</span>}
      </div>
      <Md src={g.description} />
      <details className="gap-reason" open={n <= 2}>
        <summary>Why this is a gap</summary>
        <Md src={g.reasoning} />
      </details>
      {g.grounding.length > 0 && (
        <div className="gap-ground">
          <span className="muted small">Grounded in</span>
          {g.grounding.map((gr, i) =>
            gr.kind === "author_statement" ? (
              <button key={i} type="button" className="ref-chip" onClick={() => jumpTo("oq-" + gr.ref)}>{gr.ref} (authors)</button>
            ) : (
              <span key={i} className="ground-item">
                <Refs ids={[gr.ref]} />
                {gr.quote && !gr.ref.startsWith("E") && <span className="ground-quote">“<MdInline src={gr.quote.slice(0, 220)} />{gr.quote.length > 220 ? "…" : ""}”</span>}
              </span>
            ),
          )}
          {g.builds_on.map((q) => <button key={q} type="button" className="ref-chip" onClick={() => jumpTo("oq-" + q)}>builds on {q}</button>)}
        </div>
      )}
      <div className="gap-steps"><strong>First steps.</strong> <MdInline src={g.first_steps} /></div>
      {g.relevance_to_reader && <div className="gap-you"><strong>For you.</strong> <MdInline src={g.relevance_to_reader} /></div>}
      <div className="gap-foot small muted">
        {g.novelty_check && <span><strong>Novelty check:</strong> <MdInline src={g.novelty_check} /></span>}
        {g.cite_keys.length > 0 && <span> · cites <button type="button" className="link-btn" onClick={() => src.openCites(g.cite_keys)}>{g.cite_keys.length} reference(s) from the paper</button></span>}
      </div>
    </article>
  );
}

function IdeaCard({ x, p }: { x: Idea; p: PaperDetail }) {
  return (
    <article className="idea card">
      <span className="kind-badge k-idea">Research idea</span>
      <BookmarkButton paper={p} kind="idea" title={x.title} text={x.idea} identity={x.idea} />
      <h4><MdInline src={x.title} /></h4>
      <Md src={x.idea} />
      <p><strong>Why promising.</strong> <MdInline src={x.why_promising} /></p>
      <p><strong>Risks.</strong> <MdInline src={x.risks} /></p>
      {x.relevance_to_reader && <p className="gap-you"><strong>For you.</strong> <MdInline src={x.relevance_to_reader} /></p>}
      {x.grounding.length > 0 && <div className="gap-ground"><span className="muted small">Grounded in</span> <Refs ids={x.grounding.map((g) => g.ref)} /></div>}
    </article>
  );
}

function GapsSection({ p, gaps, onRequest }: { p: PaperDetail; gaps: GapsAnalysis | null; onRequest: (model?: string) => void }) {
  const [model, setModel] = useState("auto");
  const job = p.jobs.find((j) => j.kind === "gaps" && (j.status === "pending" || j.status === "running"));
  const failed = p.jobs.find((j) => j.kind === "gaps" && j.status === "failed");
  const canRun = !!p.analyses.digest;
  return (
    <section className="block-sec" id="gaps">
      <div className="sec-title-row">
        <h2><Icon name="bulb" /> AI-inferred gaps &amp; research opportunities</h2>
        <span className="ai-label big" title="Inferred by a model, not stated by the authors">AI-inferred · not author claims</span>
      </div>
      {gaps ? (
        <>
          <p className="muted small">
            By <strong>{gaps.model}</strong>{gaps.fallback ? " (fallback: premium model unavailable)" : ""} from the verified digest, author statements and the introduction/conclusions.
            Grounding: {gaps.grounding.quotes_ok ?? 0} quotes verified, {gaps.grounding.cites_removed ?? 0} unknown citations removed.
          </p>
          {gaps.what_to_check_first && (
            <div className="check-first card"><span className="eyebrow">If you have one afternoon</span><Md src={gaps.what_to_check_first} /></div>
          )}
          <div className="gap-list">{gaps.gaps.map((g, i) => <GapCard key={i} g={g} n={i + 1} p={p} />)}</div>
          {gaps.ideas.length > 0 && <><h3>Research ideas</h3><div className="gap-list">{gaps.ideas.map((x, i) => <IdeaCard key={i} x={x} p={p} />)}</div></>}
          {gaps.assessment && (
            <details className="assessment">
              <summary><h3>Critical assessment</h3></summary>
              {gaps.assessment.strengths?.length > 0 && <><h4>Strengths</h4><ul>{gaps.assessment.strengths.map((s, i) => <li key={i}><MdInline src={s} /></li>)}</ul></>}
              {gaps.assessment.weak_points?.length > 0 && <><h4>Weak points</h4><List items={gaps.assessment.weak_points} /></>}
              {gaps.assessment.robustness && <><h4>Robustness</h4><Md src={gaps.assessment.robustness} /></>}
            </details>
          )}
          {gaps.version !== p.content?.version && <p className="muted small">Analysis of v{gaps.version}.</p>}
        </>
      ) : (
        <p className="muted">
          {job ? `Queued (${job.status})…` : canRun
            ? "Premium gap analysis runs automatically for papers above the relevance threshold (and starred ones). You can run it now."
            : "Available once the paper has a digest."}
        </p>
      )}
      {failed && !job && <p className="error small">Last attempt failed: {failed.error?.split("\n")[0]}</p>}
      {canRun && !job && (
        <div className="inline-form">
          <ModelPicker group="gaps" value={model} onChange={setModel} />
          <button type="button" className="btn btn-sm" onClick={() => onRequest(model)}>
            <Icon name="sparkle" /> {gaps ? "Re-analyze" : "Analyze gaps"}
          </button>
        </div>
      )}
    </section>
  );
}

function ChangesCard({ c }: { c: Changes }) {
  return (
    <section className="block-sec" id="changes">
      <h2>What changed in v{c.to_version} <span className={`tag ${c.major ? "tag-warn" : ""}`}>{c.major ? "substantial" : "minor"}</span></h2>
      {c.author_comment && <p className="muted small">Author comment: {c.author_comment}</p>}
      <ul className="small">
        {c.abstract_changed && <li>Abstract changed.</li>}
        {c.sections_added.length > 0 && <li>New sections: {c.sections_added.join(", ")}</li>}
        {c.sections_removed.length > 0 && <li>Removed sections: {c.sections_removed.join(", ")}</li>}
        {c.sections_changed.length > 0 && <li>Edited: {c.sections_changed.map((s) => `${s.section} (${Math.round((1 - s.similarity) * 100)}% changed)`).join("; ")}</li>}
        {c.equations_changed.length > 0 && <li>Equations changed: {c.equations_changed.map((e) => `(${e})`).join(", ")}</li>}
        <li>{c.equations_added} equations added, {c.equations_removed} removed; length {c.length_change >= 0 ? "+" : ""}{Math.round(c.length_change * 100)}%; references {c.references_change >= 0 ? "+" : ""}{c.references_change}.</li>
      </ul>
    </section>
  );
}

function FullText({ digest }: { digest: Digest | null }) {
  const src = useSource();
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const fn = (e: Event) => {
      setOpen(true);
      const id = (e as CustomEvent).detail as string;
      window.setTimeout(() => jumpTo(id), 300);
    };
    window.addEventListener("axl-open-fulltext", fn);
    return () => window.removeEventListener("axl-open-fulltext", fn);
  }, []);
  const doc = src.doc;
  return (
    <section className="block-sec" id="fulltext">
      <div className="sec-title-row">
        <h2>Full text</h2>
        <button type="button" className="btn btn-quiet btn-sm" onClick={() => setOpen(!open)}>{open ? "Collapse" : "Show parsed text"}</button>
      </div>
      {doc && <p className="muted small">Parsed from {doc.source_kind === "latex" ? "the LaTeX source" : doc.source_kind === "html" ? "arXiv HTML" : doc.source_kind === "pdf" ? "the PDF text layer (equations not recoverable)" : "the abstract only"} · {doc.stats.sections} sections, {doc.stats.equations} equations, {doc.references.length} references{doc.stats.numbering ? ` · equation numbers ${doc.stats.numbering}` : ""}.</p>}
      {open && doc && (
        <div className="fulltext">
          {doc.warnings.length > 0 && <p className="muted small">Parser notes: {doc.warnings.join("; ")}</p>}
          {doc.sections.map((s) => (
            <div key={s.id} id={`sec-${s.id}`} className={`ft-sec lvl-${s.level}`}>
              {s.level === 1 ? <h3><MdInline src={sectionLabel(s)} /></h3> : <h4><MdInline src={sectionLabel(s)} /></h4>}
              {digest?.section_gists?.[s.id] && <p className="gist"><span className="ai-label">AI summary</span> <MdInline src={digest.section_gists[s.id]} /></p>}
              {s.blocks.map((b) => (
                <div key={b.id} id={`blk-${b.id}`} className={`ft-blk ft-${b.type}`} data-id={b.id}>
                  {b.type === "equation" ? <Equation latex={b.latex ?? ""} number={b.number} /> :
                    b.type === "paragraph" ? <Md src={b.text} /> :
                      <p className="caption"><strong>{b.type === "figure" ? "Figure" : "Table"} {b.number}.</strong> <MdInline src={b.text} /></p>}
                  <span className="blk-id">{b.id}</span>
                </div>
              ))}
            </div>
          ))}
          <h3 id="references">References</h3>
          <ReferenceList refs={doc.references} />
        </div>
      )}
      {open && !doc && <p className="muted">{src.loading ? "Loading…" : "Full text not available yet."}</p>}
    </section>
  );
}

function PaperBody({ p, reload }: { p: PaperDetail; reload: () => void }) {
  const src = useSource();
  const dg = p.analyses.digest?.data ?? null;
  const gaps = p.analyses.gaps?.data ?? null;
  const tri = p.analyses.triage?.data ?? null;
  const changes = p.analyses.changes?.data ?? null;
  const rd = p.relevance_detail;
  const [msg, setMsg] = useState<string | null>(null);
  const act = async (a: string) => { await api.action(p.id, a); reload(); };
  const proc = async (what: string, model?: string) => {
    try {
      await api.process(p.id, what, model);
      setMsg(`Queued: ${what}`);
      reload();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    }
  };
  const active = p.jobs.filter((j) => j.status === "pending" || j.status === "running");
  const oqs = [...(dg?.open_questions ?? [])].sort((a, b) => (b.importance ?? 1) - (a.importance ?? 1));

  return (
    <main className="page page-wide paper-page">
      <div className="paper-grid">
        <article className="paper-main">
          <div className="crumbs">
            <Link to={p.day ? `/day/${p.day}` : "/"}>← {p.day ? fmtDay(p.day, { day: "numeric", month: "short", year: "numeric" }) : "Calendar"}</Link>
          </div>
          <header className="paper-head">
            <div className="pc-meta">
              <span className="arxiv-id">arXiv:{p.id}v{p.version}</span>
              {p.categories.map((c) => <span key={c} className="tag cat">{c}</span>)}
              <StatusTag p={p} />
            </div>
            <h1><MdInline src={p.title} /></h1>
            <p className="paper-authors"><Authors authors={p.authors} n={p.authors.length} /></p>
            <p className="muted small">
              Submitted {p.submitted?.slice(0, 10)}{p.versions.length > 1 ? ` · latest v${p.version} ${p.versions[p.versions.length - 1].date?.slice(0, 10)}` : ""}
              {p.comments ? ` · ${p.comments}` : ""}{p.journal_ref ? ` · ${p.journal_ref}` : ""}
            </p>
            <div className="paper-links">
              <a className="btn btn-quiet btn-sm" href={p.links.abs} target="_blank" rel="noreferrer">arXiv <Icon name="external" /></a>
              <a className="btn btn-quiet btn-sm" href={p.links.pdf} target="_blank" rel="noreferrer">PDF <Icon name="external" /></a>
              <a className="btn btn-quiet btn-sm" href={p.links.html} target="_blank" rel="noreferrer">HTML <Icon name="external" /></a>
              <a className="btn btn-quiet btn-sm" href={p.links.inspire} target="_blank" rel="noreferrer">INSPIRE <Icon name="external" /></a>
              <span className="spacer" />
              <BookmarkButton paper={p} />
              <button type="button" className={`btn btn-sm ${p.starred ? "" : "btn-quiet"}`} onClick={() => act(p.starred ? "unstar" : "star")}><Icon name="star" /> {p.starred ? "Starred" : "Star"}</button>
              <button type="button" className="btn btn-quiet btn-sm" onClick={() => act(p.read ? "unread" : "read")}><Icon name="check" /> {p.read ? "Read" : "Mark read"}</button>
            </div>
          </header>

          <section className="glance card" id="glance">
            <div className="glance-rel">
              <RelevanceBadge value={p.relevance} />
              <div className="small">
                <strong>Why it's ranked here.</strong>{" "}
                {rd.model_reason ? <MdInline src={rd.model_reason} /> : "Keyword match only (triage pending)."}
                <div className="muted">
                  keywords {rd.keyword?.toFixed(1) ?? "–"} · model {rd.model ?? "–"}{rd.boost ? ` · boost +${rd.boost}` : ""}
                  {rd.interests?.length ? ` · ${rd.interests.map((i) => `${i.interest} (${i.matched.slice(0, 3).join(", ")})`).join("; ")}` : ""}
                </div>
              </div>
            </div>
            {dg ? (
              <>
                <p className="tldr"><MdInline src={dg.tldr} /></p>
                <p className="central-q"><span className="eyebrow">Central question</span><br /><MdInline src={dg.central_question} /></p>
                {dg.reading_guide && (
                  <div className="guide small">
                    <span><strong>Difficulty</strong> {"●".repeat(dg.reading_guide.difficulty)}{"○".repeat(5 - dg.reading_guide.difficulty)}</span>
                    <span><strong>For</strong> <MdInline src={dg.reading_guide.audience} /></span>
                    {dg.reading_guide.prerequisites.length > 0 && <span><strong>Background</strong> {dg.reading_guide.prerequisites.join(", ")}</span>}
                    {dg.reading_guide.sections_to_read.length > 0 && (
                      <span><strong>Read</strong> {dg.reading_guide.sections_to_read.map((s, i) => (
                        <button key={s} type="button" className="ref-chip" onClick={() => jumpTo(s)}>{i > 0 ? "" : ""}{sectionLabel(src.section(s)) || s}</button>
                      ))}</span>
                    )}
                  </div>
                )}
              </>
            ) : (
              <>
                {tri?.one_liner && <p className="tldr"><MdInline src={tri.one_liner} /></p>}
                <details open={!tri}><summary className="small">Abstract</summary><Md src={p.abstract} /></details>
                <p className="muted small">{active.length ? `Processing: ${active.map((j) => `${j.kind} (${j.status})`).join(", ")}` : "Digest not available yet."}</p>
                {!active.some((j) => j.kind === "digest") && (
                  <button type="button" className="btn btn-sm" onClick={() => proc("digest")}><Icon name="book" /> Request full digest</button>
                )}
                {msg && <p role="status" className="small">{msg}</p>}
              </>
            )}
          </section>

          {changes && <ChangesCard c={changes} />}

          <section className="block-sec" id="open">
            <div className="sec-title-row">
              <h2><Icon name="flag" /> Open problems stated by the authors</h2>
              <span className="verified-label" title="Every quote below was checked verbatim against the paper">verified quotes</span>
            </div>
            {dg ? (
              oqs.length ? <ul className="oq-list">{oqs.map((q) => <AuthorQuestion key={q.id} q={q} p={p} />)}</ul>
                : <p className="muted">The authors state no explicit open questions, limitations or future directions{dg.depth === "light" ? " in the introduction and conclusions (light digest)" : ""}.</p>
            ) : <p className="muted">Extracted with the digest.</p>}
          </section>

          <GapsSection p={p} gaps={gaps} onRequest={(m) => proc("gaps", m)} />

          {dg && (
            <section className="block-sec digest" id="digest">
              <div className="sec-title-row">
                <h2><Icon name="book" /> Digest</h2>
                <span className="muted small">{dg.depth === "full" ? `full text, ${dg.chunks} chunk(s)` : "light: intro, conclusions, open-problem passages"} · {dg.models.join(", ")}</span>
              </div>
              <h3>Physical motivation</h3><Md src={dg.physical_motivation} />
              <h3>The intuition</h3><Md src={dg.intuition} />
              <h3>Assumptions</h3><List items={dg.assumptions} />
              <h3>Methods</h3><List items={dg.methods} />
              {dg.key_equations.length > 0 && (
                <>
                  <h3>Key equations</h3>
                  <div className="key-eqs">
                    {dg.key_equations.map((e) => (
                      <div key={e.id} className="key-eq card">
                        <Equation latex={e.latex} number={e.number} />
                        <p><MdInline src={e.meaning} /></p>
                        {e.role && <p className="muted small"><strong>Role.</strong> <MdInline src={e.role} /></p>}
                        <Refs ids={[e.id]} />
                      </div>
                    ))}
                  </div>
                </>
              )}
              <h3>Results</h3><List items={dg.results} />
              <h3>Limitations acknowledged by the authors</h3><List items={dg.limitations} />
              <h3>Implications</h3><Md src={dg.implications} />
              <h3>Takeaways</h3>
              <ul className="takeaways">{dg.takeaways.map((t, i) => <li key={i}><MdInline src={t} /></li>)}</ul>
              {dg.key_citations.length > 0 && (
                <>
                  <h3>Builds most directly on</h3>
                  <ul className="refd small">{dg.key_citations.map((c) => (
                    <li key={c.key}><button type="button" className="ref-chip" onClick={() => src.openCites([c.key])}>[{c.n}]</button> <MdInline src={c.role} />
                      {c.arxiv && <> · <a href={`https://arxiv.org/abs/${c.arxiv}`} target="_blank" rel="noreferrer">arXiv:{c.arxiv}</a></>}</li>
                  ))}</ul>
                </>
              )}
            </section>
          )}

          <FullText digest={dg} />

          <section className="block-sec provenance" id="provenance">
            <h2>Provenance &amp; processing</h2>
            {p.content ? (
              <dl className="prov">
                <dt>Source</dt><dd>{p.content.provenance.source_kind} · <a href={p.content.provenance.url} target="_blank" rel="noreferrer">{p.content.provenance.url}</a> · {(p.content.provenance.bytes / 1024).toFixed(0)} kB · sha256 {p.content.provenance.sha256?.slice(0, 12)}{p.content.provenance.main_file ? ` · main file ${p.content.provenance.main_file}` : ""}</dd>
                <dt>Attempts</dt><dd>{p.content.provenance.attempts.map((a) => `${a.kind}: ${a.result}`).join(" → ")}</dd>
                <dt>Fetched</dt><dd>v{p.content.version}, {ago(p.content.fetched_at)}{p.content.provenance.fetched_by ? ` by ${p.content.provenance.fetched_by}` : ""}</dd>
                {dg && <><dt>Digest</dt><dd>{dg.depth} · {dg.calls} model calls ({dg.cached_calls} cached) · {dg.models.join(", ")} · {dg.prompt_versions.join(", ")}{dg.escalated ? " · escalated" : ""}</dd>
                  <dt>Grounding</dt><dd>{Object.entries(dg.grounding).map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`).join(" · ")}</dd></>}
                {p.analyses.gaps && <><dt>Gaps</dt><dd>{p.analyses.gaps.model} · {p.analyses.gaps.prompt_version} · {ago(p.analyses.gaps.created_at)}</dd></>}
              </dl>
            ) : <p className="muted">Full text not fetched yet.</p>}
            <div className="inline-form">
              <button type="button" className="btn btn-quiet btn-sm" onClick={() => proc("digest")}><Icon name="refresh" /> {dg ? "Re-run full digest" : "Digest now (full)"}</button>
              <button type="button" className="btn btn-quiet btn-sm" onClick={() => proc("refetch")}>Re-fetch source</button>
              <button type="button" className="btn btn-quiet btn-sm" onClick={() => act(p.hidden ? "unhide" : "hide")}>{p.hidden ? "Unhide" : "Hide paper"}</button>
              {msg && <span className="muted small">{msg}</span>}
            </div>
            {p.jobs.length > 0 && (
              <details className="small"><summary>Jobs ({p.jobs.length})</summary>
                <ul className="job-mini">{p.jobs.map((j) => <li key={j.id}><span className={`badge badge-${j.status}`}>{j.status}</span> {j.kind} {j.target} · {ago(j.updated_at)}{j.worker ? ` · ${j.worker}` : ""}{j.error ? <div className="error">{j.error.split("\n")[0]}</div> : null}</li>)}</ul>
              </details>
            )}
          </section>
        </article>

        <aside className="paper-side">
          <nav className="toc card">
            <a href="#glance">At a glance</a>
            {changes && <a href="#changes">What changed</a>}
            <a href="#open">Author open problems {dg ? <span className="count">{oqs.length}</span> : null}</a>
            <a href="#gaps">AI gaps &amp; ideas {gaps ? <span className="count">{gaps.gaps.length + gaps.ideas.length}</span> : null}</a>
            {dg && <a href="#digest">Digest</a>}
            <a href="#fulltext">Full text</a>
            <a href="#provenance">Provenance</a>
          </nav>
          {active.length > 0 && <div className="card side-note small"><span className="live-dot" /> {active.map((j) => `${j.kind}: ${j.status}`).join(" · ")}</div>}
          {dg && dg.takeaways.length > 0 && (
            <div className="card side-note">
              <span className="eyebrow">Remember</span>
              <ul className="small">{dg.takeaways.slice(0, 3).map((t, i) => <li key={i}><MdInline src={t} /></li>)}</ul>
            </div>
          )}
        </aside>
      </div>
    </main>
  );
}
