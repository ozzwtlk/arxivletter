import { Link } from "react-router-dom";
import type { PaperCard as Card } from "../api/types";
import { relClass } from "../lib/hooks";
import { Icon } from "./Icon";
import { MdInline } from "./Md";

import { BookmarkButton } from "./Bookmarks";

export function RelevanceBadge({ value, title }: { value: number | null | undefined; title?: string }) {
  return (
    <span className={`rel-badge ${relClass(value)}`} title={title ?? "Relevance to your research profile (0–10)"}>
      {value == null ? "–" : value.toFixed(1)}
    </span>
  );
}

export function StatusTag({ p }: { p: Pick<Card, "status" | "digest_depth" | "n_gaps"> }) {
  if (p.status === "digested")
    return (
      <span className={`tag ${p.digest_depth === "full" ? "tag-full" : "tag-light"}`} title={p.digest_depth === "full" ? "Full map-reduce digest of the whole text" : "Light digest: introduction, conclusions and open-problem passages"}>
        {p.digest_depth === "full" ? "full digest" : "light digest"}
      </span>
    );
  const label: Record<string, string> = { new: "queued", fetched: "full text ready", triaged: "triaged", failed: "failed" };
  return <span className="tag">{label[p.status] ?? p.status}</span>;
}

export function Authors({ authors, n }: { authors: string[]; n: number }) {
  const shown = authors.slice(0, 5);
  return (
    <span className="authors">
      {shown.join(", ")}
      {n > shown.length && <span className="muted"> + {n - shown.length} more</span>}
    </span>
  );
}

export function PaperCardView({ p, onStar }: { p: Card; onStar?: (p: Card) => void }) {
  const summary = p.tldr || p.one_liner;
  return (
    <article className={`paper-card card ${p.read ? "is-read" : ""}`}>
      <div className="pc-rel">
        <RelevanceBadge value={p.relevance} title={p.relevance_reason ?? undefined} />
      </div>
      <div className="pc-main">
        <div className="pc-meta">
          <span className="arxiv-id">{p.id}</span>
          {p.event_kind === "revision" ? <span className="tag tag-rev">v{p.event_version} revision</span> : null}
          {p.primary_category && <span className={`tag cat ${p.categories.slice(0, 1).some((c) => c === "hep-ph" || c === "hep-th") ? "" : "cross"}`}>{p.primary_category}</span>}
          {p.categories.length > 1 && <span className="muted small">+{p.categories.slice(1).join(", ")}</span>}
          <span className="spacer" />
          <StatusTag p={p} />
          <BookmarkButton paper={p} />
          {onStar && (
            <button type="button" className={`star-btn ${p.starred ? "on" : ""}`} onClick={() => onStar(p)} title={p.starred ? "Unstar" : "Star: always get a full digest and premium gap analysis"}>
              <Icon name="star" />
            </button>
          )}
        </div>
        <h3 className="pc-title">
          <Link to={`/paper/${p.id}`}>
            <MdInline src={p.title} />
          </Link>
        </h3>
        <div className="pc-authors"><Authors authors={p.authors} n={p.n_authors} /></div>
        {summary && <p className="pc-summary"><MdInline src={summary} /></p>}
        {(p.top_open_question || p.top_gap) && (
          <div className="pc-gaps">
            {p.top_open_question && (
              <div className="pc-gap author" title="Stated by the authors (verified quote)">
                <span className="gap-src">Authors</span>
                <MdInline src={p.top_open_question} />
                {p.n_open_questions > 1 && <span className="muted small"> · +{p.n_open_questions - 1}</span>}
              </div>
            )}
            {p.top_gap && (
              <div className="pc-gap ai" title="AI-inferred research opportunity">
                <span className="gap-src">AI</span>
                <MdInline src={p.top_gap} />
                {p.n_gaps > 1 && <span className="muted small"> · +{p.n_gaps - 1}</span>}
              </div>
            )}
          </div>
        )}
        {p.topics.length > 0 && (
          <div className="pc-topics">
            {p.topics.map((t) => <span key={t} className="topic">{t}</span>)}
          </div>
        )}
      </div>
    </article>
  );
}
