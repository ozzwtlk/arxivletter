import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { Brief, PaperCard } from "../api/types";
import { Calendar } from "../components/Calendar";
import { Icon } from "../components/Icon";
import { Md, MdInline } from "../components/Md";
import { PaperCardView } from "../components/PaperCard";
import { fmtDay, useLoad } from "../lib/hooks";
import { load, save } from "../lib/storage";

interface Filters {
  topics: string[];
  cats: string[];
  kind: "all" | "new" | "revision";
  minRel: number;
  digestedOnly: boolean;
  hideRead: boolean;
  q: string;
  sort: "relevance" | "time";
}
const DEFAULT: Filters = { topics: [], cats: [], kind: "new", minRel: 0, digestedOnly: false, hideRead: false, q: "", sort: "relevance" };

function BriefCard({ brief, papers, day, status, onMake }: { brief: Brief | null; papers: PaperCard[]; day: string; status: string | null; onMake: () => void }) {
  const byId = new Map(papers.map((p) => [p.id, p]));
  const link = (id: string) => <Link key={id} to={`/paper/${id}`} className="pid">{byId.get(id) ? <MdInline src={byId.get(id)!.title} /> : id}</Link>;
  if (!brief) {
    return (
      <div className="brief card brief-empty">
        <div>
          <span className="eyebrow">Daily brief</span>
          <p className="muted small">
            {status === "pending" || status === "running"
              ? "Being written by the premium model…"
              : "A cross-paper synthesis is written once most of the day's relevant papers have digests."}
          </p>
        </div>
        {status !== "pending" && status !== "running" && (
          <button type="button" className="btn btn-quiet btn-sm" onClick={onMake}><Icon name="sparkle" /> Write brief now</button>
        )}
      </div>
    );
  }
  return (
    <section className="brief card" aria-label={`Brief for ${day}`}>
      <div className="brief-head">
        <span className="eyebrow">Daily brief · AI synthesis</span>
        <span className="muted small" title="Model that wrote the brief">{brief.model}</span>
      </div>
      <h2 className="brief-headline"><MdInline src={brief.headline} /></h2>
      <Md src={brief.overview.replace(/\[arXiv:([^\]]+)\]/g, "[arXiv:$1](/paper/$1)")} className="brief-overview" />
      {brief.must_read.length > 0 && (
        <div className="brief-block">
          <h3>Must read</h3>
          <ul>{brief.must_read.map((m) => <li key={m.id}>{link(m.id)} — <MdInline src={m.why} /></li>)}</ul>
        </div>
      )}
      {brief.themes.length > 0 && (
        <details className="brief-block">
          <summary><h3>Themes ({brief.themes.length})</h3></summary>
          {brief.themes.map((t) => (
            <div key={t.title} className="brief-theme">
              <strong><MdInline src={t.title} /></strong> <Md src={t.summary} />
              <div className="small">{t.papers.map((id, i) => <span key={id}>{i > 0 && " · "}{link(id)}</span>)}</div>
            </div>
          ))}
        </details>
      )}
      {brief.cross_paper_opportunities.length > 0 && (
        <details className="brief-block" open>
          <summary><h3>Cross-paper opportunities <span className="ai-label">AI-inferred</span></h3></summary>
          {brief.cross_paper_opportunities.map((t) => (
            <div key={t.title} className="brief-theme">
              <strong><MdInline src={t.title} /></strong> <Md src={t.description} />
              <div className="small">{t.papers.map((id, i) => <span key={id}>{i > 0 && " · "}{link(id)}</span>)}</div>
            </div>
          ))}
        </details>
      )}
    </section>
  );
}

export function Home() {
  const { day: dayParam } = useParams();
  const nav = useNavigate();
  const [month, setMonth] = useState<string | null>(dayParam ? dayParam.slice(0, 7) : null);
  const [filters, setFiltersState] = useState<Filters>(() => ({ ...DEFAULT, ...load<Partial<Filters>>("axl.filters", {}), q: "" }));
  const setFilters = (f: Partial<Filters>) => {
    const next = { ...filters, ...f };
    setFiltersState(next);
    save("axl.filters", { ...next, q: "" });
  };
  const cal = useLoad(() => api.calendar(month ?? undefined), [month], () => 60000);
  const day = dayParam ?? cal.data?.latest_day ?? null;
  const dayData = useLoad(() => (day ? api.day(day) : Promise.resolve(null)), [day], (d) =>
    d && d.papers.some((p) => p.status !== "digested") ? 20000 : 60000);

  const papers = dayData.data?.papers ?? [];
  const topicCounts = useMemo(() => {
    const m = new Map<string, number>();
    papers.filter((p) => filters.kind === "all" || (p.event_kind ?? "new") === filters.kind).forEach((p) => p.topics.forEach((t) => m.set(t, (m.get(t) ?? 0) + 1)));
    return [...m.entries()].sort((a, b) => b[1] - a[1]);
  }, [papers, filters.kind]);
  const shown = useMemo(() => {
    const q = filters.q.trim().toLowerCase();
    let list = papers.filter((p) =>
      (filters.kind === "all" || (p.event_kind ?? "new") === filters.kind) &&
      (filters.topics.length === 0 || filters.topics.some((t) => p.topics.includes(t))) &&
      (filters.cats.length === 0 || filters.cats.some((c) => (c === "cross" ? !["hep-ph", "hep-th"].includes(p.primary_category ?? "") : p.primary_category === c))) &&
      (p.relevance ?? 0) >= filters.minRel &&
      (!filters.digestedOnly || p.status === "digested") &&
      (!filters.hideRead || !p.read) &&
      (!q || `${p.title} ${p.authors.join(" ")} ${p.tldr ?? ""} ${p.one_liner ?? ""} ${p.id}`.toLowerCase().includes(q)),
    );
    if (filters.sort === "time") list = [...list].sort((a, b) => (a.submitted ?? "").localeCompare(b.submitted ?? ""));
    return list;
  }, [papers, filters]);

  const toggle = (arr: string[], v: string) => (arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v]);
  const star = async (p: PaperCard) => {
    await api.action(p.id, p.starred ? "unstar" : "star");
    dayData.reload();
  };
  const stats = cal.data?.days.find((d) => d.day === day);
  const curMonth = month ?? cal.data?.month ?? new Date().toISOString().slice(0, 7);

  return (
    <main className="page page-wide home">
      <aside className="home-side">
        <Calendar month={curMonth} days={cal.data?.days ?? []} selected={day} onMonth={setMonth} onSelect={(d) => nav(`/day/${d}`)} />
        <div className="filters card">
          <div className="filter-row">
            <div className="seg" role="group" aria-label="Submission kind">
              {(["new", "revision", "all"] as const).map((k) => (
                <button key={k} type="button" className={filters.kind === k ? "on" : ""} onClick={() => setFilters({ kind: k })}>
                  {k === "new" ? "New" : k === "revision" ? "Revisions" : "All"}
                </button>
              ))}
            </div>
          </div>
          <label className="field">
            <span>Minimum relevance: <strong>{filters.minRel.toFixed(1)}</strong></span>
            <input type="range" min={0} max={9} step={0.5} value={filters.minRel} onChange={(e) => setFilters({ minRel: Number(e.target.value) })} />
          </label>
          <div className="chips">
            {["hep-ph", "hep-th", "cross"].map((c) => (
              <button key={c} type="button" className={`chip ${filters.cats.includes(c) ? "on" : ""}`} onClick={() => setFilters({ cats: toggle(filters.cats, c) })}>
                {c === "cross" ? "cross-lists" : c}
              </button>
            ))}
          </div>
          <div className="chips topics-filter">
            {topicCounts.map(([t, n]) => (
              <button key={t} type="button" className={`chip ${filters.topics.includes(t) ? "on" : ""}`} onClick={() => setFilters({ topics: toggle(filters.topics, t) })}>
                {t} <span className="chip-n">{n}</span>
              </button>
            ))}
          </div>
          <label className="check"><input type="checkbox" checked={filters.digestedOnly} onChange={(e) => setFilters({ digestedOnly: e.target.checked })} /> Digested only</label>
          <label className="check"><input type="checkbox" checked={filters.hideRead} onChange={(e) => setFilters({ hideRead: e.target.checked })} /> Hide papers marked read</label>
          {(filters.topics.length > 0 || filters.cats.length > 0 || filters.minRel > 0 || filters.digestedOnly || filters.hideRead) && (
            <button type="button" className="link-btn small" onClick={() => setFilters({ ...DEFAULT, kind: filters.kind })}>Reset filters</button>
          )}
        </div>
      </aside>

      <section className="home-main">
        {!day && !cal.loading && (
          <div className="empty card">
            <h2>No papers yet</h2>
            <p>The scheduler harvests new hep-ph and hep-th submissions every hour. Start one now in <Link to="/ops">Operations</Link>.</p>
          </div>
        )}
        {day && (
          <>
            <div className="day-head">
              <div>
                <span className="eyebrow">Submitted on (UTC)</span>
                <h1>{fmtDay(day)}</h1>
                {stats && (
                  <p className="muted day-stats">
                    {stats.new} new · {stats.revisions} revisions · <strong>{stats.relevant}</strong> highly relevant · {stats.digested} digested
                  </p>
                )}
              </div>
              <div className="day-tools">
                <input className="input" type="search" placeholder="Filter this day…" value={filters.q} onChange={(e) => setFilters({ q: e.target.value })} />
                <select className="input" value={filters.sort} onChange={(e) => setFilters({ sort: e.target.value as Filters["sort"] })}>
                  <option value="relevance">By relevance</option>
                  <option value="time">By submission time</option>
                </select>
              </div>
            </div>
            {dayData.data && (
              <BriefCard brief={dayData.data.brief} papers={papers} day={day} status={dayData.data.brief_status}
                onMake={async () => { await api.brief(day); dayData.reload(); }} />
            )}
            {dayData.error && <p className="error">{dayData.error}</p>}
            <p className="muted small list-count">{shown.length} of {papers.length} papers</p>
            <div className="paper-list">
              {shown.map((p) => <PaperCardView key={p.id} p={p} onStar={star} />)}
            </div>
          </>
        )}
      </section>
    </main>
  );
}
