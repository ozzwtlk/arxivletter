import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { PaperCardView } from "../components/PaperCard";
import { useLoad } from "../lib/hooks";

export function SearchPage() {
  const [sp, setSp] = useSearchParams();
  const q = sp.get("q") ?? "";
  const starred = sp.get("starred") === "1";
  const [text, setText] = useState(q);
  const [sort, setSort] = useState("relevance");
  const { data, loading, error, reload } = useLoad(() => (q || starred ? api.papers({ q, starred, sort, limit: 100 }) : Promise.resolve(null)), [q, starred, sort]);
  return (
    <main className="page page-wide search-page">
      <form className="search-form" onSubmit={(e) => { e.preventDefault(); setSp(text ? { q: text } : {}); }}>
        <input className="input big" type="search" autoFocus placeholder="Search titles, authors, abstracts and digests — or an arXiv id" value={text} onChange={(e) => setText(e.target.value)} />
        <select className="input" value={sort} onChange={(e) => setSort(e.target.value)}><option value="relevance">Relevance</option><option value="date">Newest</option></select>
        <button className="btn" type="submit">Search</button>
        <button className="btn btn-quiet" type="button" onClick={() => setSp({ starred: "1" })}>★ Starred</button>
      </form>
      {error && <p className="error">{error}</p>}
      {loading && <p className="muted">Searching…</p>}
      {data && <p className="muted small">{data.total} papers</p>}
      <div className="paper-list">{data?.papers.map((p) => <PaperCardView key={p.id} p={p} onStar={async (x) => { await api.action(x.id, x.starred ? "unstar" : "star"); reload(); }} />)}</div>
    </main>
  );
}
