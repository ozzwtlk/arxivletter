import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { AuthorStatement, Gap, GapItem, Idea } from "../api/types";
import { BookmarkButton } from "../components/Bookmarks";
import { Icon } from "../components/Icon";
import { Md, MdInline } from "../components/Md";
import { RelevanceBadge } from "../components/PaperCard";
import { KIND_LABEL, useLoad } from "../lib/hooks";

const iso = (d: Date) => d.toISOString().slice(0, 10);

function Item({ it }: { it: GapItem }) {
  const paper = { id: it.paper_id, title: it.title };
  const head = (
    <div className="gi-paper small">
      <RelevanceBadge value={it.relevance} />
      <Link to={`/paper/${it.paper_id}`}><MdInline src={it.title} /></Link>
      <span className="muted">· {it.authors.join(", ")}{it.authors.length >= 3 ? " et al." : ""} · {it.day}</span>
    </div>
  );
  if (it.source === "author") {
    const q = it.item as AuthorStatement;
    return (
      <li className="gi card gi-author">
        <div className="gi-top"><span className="gap-src">Authors</span><span className={`kind-badge k-${q.kind}`}>{KIND_LABEL[q.kind] ?? q.kind}</span>{q.importance === 3 && <span className="tag tag-key">central</span>}</div>
        <BookmarkButton paper={paper} kind="question" title={q.summary || "Author statement"} text={q.quote} identity={q.quote} />
        {q.summary && <p><MdInline src={q.summary} /></p>}
        <blockquote className="quote"><Md src={q.quote} /><span className="quote-tag">verbatim · {q.section_number ? `§${q.section_number} ` : ""}{q.section_title}</span></blockquote>
        {head}
      </li>
    );
  }
  if (it.source === "idea") {
    const x = it.item as Idea;
    return (
      <li className="gi card gi-ai">
        <div className="gi-top"><span className="gap-src ai">AI</span><span className="kind-badge k-idea">Research idea</span><span className="muted small">{it.model}</span></div>
        <BookmarkButton paper={paper} kind="idea" title={x.title} text={x.idea} identity={x.idea} />
        <h3><MdInline src={x.title} /></h3>
        <Md src={x.idea} />
        {x.relevance_to_reader && <p className="gap-you small"><strong>For you.</strong> <MdInline src={x.relevance_to_reader} /></p>}
        {head}
      </li>
    );
  }
  const g = it.item as Gap;
  return (
    <li className="gi card gi-ai">
      <div className="gi-top">
        <span className="gap-src ai">AI</span><span className="kind-badge k-ai">{KIND_LABEL[g.kind] ?? g.kind}</span>
        <span className="muted small">impact {g.impact}/5 · difficulty {g.difficulty}/5 · {g.confidence} confidence · {it.model}</span>
      </div>
      <BookmarkButton paper={paper} kind="gap" title={g.title} text={g.description} identity={g.description} />
      <h3><MdInline src={g.title} /></h3>
      <Md src={g.description} />
      <details><summary className="small">Reasoning &amp; first steps</summary><Md src={g.reasoning} /><p><strong>First steps.</strong> <MdInline src={g.first_steps} /></p></details>
      {head}
    </li>
  );
}

export function GapsPage() {
  const [end, setEnd] = useState(iso(new Date()));
  const [start, setStart] = useState(iso(new Date(Date.now() - 14 * 864e5)));
  const [source, setSource] = useState("all");
  const [topic, setTopic] = useState("");
  const [minRel, setMinRel] = useState(4);
  const topics = useLoad(() => api.topics(), []);
  const { data, loading, error } = useLoad(() => api.gaps({ start, end, source, topic, min_relevance: minRel }), [start, end, source, topic, minRel]);
  return (
    <main className="page page-wide">
      <div className="page-head">
        <div>
          <span className="eyebrow">What to investigate next</span>
          <h1><Icon name="bulb" /> Research gaps</h1>
          <p className="muted">Open problems the authors state (verbatim, verified) and AI-inferred opportunities (labelled), ranked by relevance to you.</p>
        </div>
      </div>
      <div className="toolbar card">
        <label className="field"><span>From</span><input className="input" type="date" value={start} onChange={(e) => setStart(e.target.value)} /></label>
        <label className="field"><span>To</span><input className="input" type="date" value={end} onChange={(e) => setEnd(e.target.value)} /></label>
        <label className="field"><span>Source</span>
          <select className="input" value={source} onChange={(e) => setSource(e.target.value)}>
            <option value="all">Authors + AI</option><option value="author">Stated by authors</option><option value="ai">AI-inferred</option>
          </select>
        </label>
        <label className="field"><span>Topic</span>
          <select className="input" value={topic} onChange={(e) => setTopic(e.target.value)}>
            <option value="">All topics</option>
            {topics.data?.map((t) => <option key={t.name} value={t.name}>{t.name}</option>)}
          </select>
        </label>
        <label className="field"><span>Min. relevance {minRel}</span><input type="range" min={0} max={9} step={0.5} value={minRel} onChange={(e) => setMinRel(Number(e.target.value))} /></label>
      </div>
      {error && <p className="error">{error}</p>}
      {loading && !data && <p className="muted">Loading…</p>}
      {data && <p className="muted small">{data.items.length} of {data.total} items</p>}
      <ul className="gap-feed">{data?.items.map((it, i) => <Item key={i} it={it} />)}</ul>
    </main>
  );
}
