import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api, type Bookmark } from "../api/client";

const labels = { paper: "Paper", question: "Author statement", gap: "AI research gap", idea: "AI research idea" };
const Store = createContext({
  items: null as Bookmark[] | null, error: "", change: async (_item: Bookmark, _saved: boolean) => {},
});

export function BookmarksProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Bookmark[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let current = true;
    api.bookmarks().then(({ items }) => { if (current) setItems(items); },
      (e) => { if (current) setError(String(e.message ?? e)); });
    return () => { current = false; };
  }, []);
  async function change(item: Bookmark, saved: boolean) {
    await api.setBookmark(item, saved);
    setItems((old) => saved ? [item, ...(old ?? []).filter((b) => b.key !== item.key)] : (old ?? []).filter((b) => b.key !== item.key));
  }
  return <Store.Provider value={{ items, error, change }}>{children}</Store.Provider>;
}

export function BookmarkButton({ paper, kind = "paper", title, text = "", identity }: {
  paper: { id: string; title: string }; kind?: Bookmark["kind"]; title?: string; text?: string; identity?: string;
}) {
  const label = title ?? paper.title;
  const key = JSON.stringify([kind, paper.id, kind === "paper" ? "" : identity ?? label]);
  return <Toggle item={{ key, kind, paper_id: paper.id, paper_title: paper.title, title: label, text }} />;
}

function Toggle({ item }: { item: Bookmark }) {
  const { items, error, change } = useContext(Store);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState("");
  const saved = items?.some((b) => b.key === item.key) ?? false;
  return <>
    <button type="button" className="btn btn-quiet btn-sm" aria-pressed={saved}
      aria-label={`${saved ? "Remove bookmark for" : "Bookmark"} ${labels[item.kind].toLowerCase()}`}
      title={error || (saved ? "Remove bookmark" : "Save bookmark")}
      disabled={busy || !items}
      onClick={async () => {
        setBusy(true); setFailure("");
        try { await change(item, !saved); }
        catch (e) { setFailure(e instanceof Error ? e.message : String(e)); }
        finally { setBusy(false); }
      }}>{saved ? "Saved" : "Save"}</button>
    {failure && <span role="alert" className="small">{failure}</span>}
  </>;
}

export function BookmarksPage() {
  const { items, error } = useContext(Store);
  const [kind, setKind] = useState("all");
  const visible = items?.filter((b) => kind === "all" || b.kind === kind);
  return <main className="page">
    <h1>Bookmarks</h1>
    <label className="field"><span>Show</span><select className="input" value={kind} onChange={(e) => setKind(e.target.value)}>
      <option value="all">All bookmarks</option>
      {Object.entries(labels).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
    </select></label>
    {error ? <p role="alert">{error}</p> : !visible ? <p>Loading…</p> : !visible.length ? <p>No bookmarks here yet.</p> : (
      <ul className="gap-list" style={{ padding: 0, listStyle: "none" }}>
        {visible.map((item) => <li key={item.key} className="gi card">
          <div className="gi-top"><span className="tag">{labels[item.kind]}</span><span className="spacer" /><Toggle item={item} /></div>
          <h3>{item.title}</h3>
          {item.text && <p style={{ whiteSpace: "pre-wrap" }}>{item.text}</p>}
          <Link to={`/paper/${item.paper_id}`}>{item.kind === "paper" ? "Open paper" : item.paper_title} · arXiv:{item.paper_id}</Link>
        </li>)}
      </ul>
    )}
  </main>;
}
