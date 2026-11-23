import { useState } from "react";
import { BrowserRouter, Link, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { BookmarksPage, BookmarksProvider } from "./components/Bookmarks";
import { Icon } from "./components/Icon";
import { useTheme } from "./lib/theme";
import { GapsPage } from "./pages/GapsPage";
import { Home } from "./pages/Home";
import { OpsPage } from "./pages/OpsPage";
import { PaperPage } from "./pages/PaperPage";
import { SearchPage } from "./pages/SearchPage";
import { SettingsPage } from "./pages/SettingsPage";

function TopBar() {
  const loc = useLocation();
  const nav = useNavigate();
  const { effective, toggle } = useTheme();
  const [q, setQ] = useState("");
  const is = (p: string) => (p === "/" ? loc.pathname === "/" || loc.pathname.startsWith("/day/") : loc.pathname.startsWith(p));
  return (
    <header className="topbar">
      <Link to="/" className="brand" aria-label="arXivletter home">
        <span className="brand-mark" aria-hidden="true">χ</span>
        <span className="brand-name">arXivletter</span>
      </Link>
      <nav className="topnav">
        <Link to="/" className={is("/") ? "active" : ""}><Icon name="calendar" /> Calendar</Link>
        <Link to="/gaps" className={is("/gaps") ? "active" : ""}><Icon name="bulb" /> Research gaps</Link>
        <Link to="/bookmarks" className={is("/bookmarks") ? "active" : ""}>Bookmarks</Link>
        <Link to="/ops" className={is("/ops") ? "active" : ""}><Icon name="server" /> Operations</Link>
        <Link to="/settings" className={is("/settings") ? "active" : ""}><Icon name="gear" /> Settings</Link>
      </nav>
      <span className="spacer" />
      <form className="search" onSubmit={(e) => { e.preventDefault(); if (q.trim()) nav(`/search?q=${encodeURIComponent(q.trim())}`); }}>
        <input type="search" placeholder="Search papers, digests, arXiv id…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search" />
      </form>
      <button type="button" className="icon-btn" onClick={toggle} title="Toggle theme" aria-label="Toggle theme">
        <Icon name={effective === "dark" ? "sun" : "moon"} />
      </button>
    </header>
  );
}

export function App() {
  return (
    <BrowserRouter>
      <BookmarksProvider>
      <TopBar />
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/day/:day" element={<Home />} />
        <Route path="/paper/*" element={<PaperPage />} />
        <Route path="/gaps" element={<GapsPage />} />
        <Route path="/bookmarks" element={<BookmarksPage />} />
        <Route path="/search" element={<SearchPage />} />
        <Route path="/ops" element={<OpsPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<main className="page"><p>Not found. <Link to="/">Calendar</Link></p></main>} />
      </Routes>
      </BookmarksProvider>
    </BrowserRouter>
  );
}
