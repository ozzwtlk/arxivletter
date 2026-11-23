import type { CalendarDay } from "../api/types";
import { Icon } from "./Icon";

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function shiftMonth(month: string, d: number): string {
  const [y, m] = month.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1 + d, 1));
  return `${dt.getUTCFullYear()}-${String(dt.getUTCMonth() + 1).padStart(2, "0")}`;
}

/** Month grid organised by arXiv submission date (UTC). Each day: new submissions, revisions and a heat bar for
 *  the number of papers above the "relevant" threshold. */
export function Calendar({ month, days, selected, onMonth, onSelect }: {
  month: string;
  days: CalendarDay[];
  selected: string | null;
  onMonth: (m: string) => void;
  onSelect: (day: string) => void;
}) {
  const [y, m] = month.split("-").map(Number);
  const first = new Date(Date.UTC(y, m - 1, 1));
  const lead = (first.getUTCDay() + 6) % 7;
  const n = new Date(Date.UTC(y, m, 0)).getUTCDate();
  const byDay = new Map(days.map((d) => [d.day, d]));
  const maxRel = Math.max(1, ...days.map((d) => d.relevant));
  const today = new Date().toISOString().slice(0, 10);
  const cells: (string | null)[] = [...Array(lead).fill(null), ...Array.from({ length: n }, (_, i) => `${month}-${String(i + 1).padStart(2, "0")}`)];
  const label = first.toLocaleDateString(undefined, { month: "long", year: "numeric", timeZone: "UTC" });
  return (
    <div className="calendar card">
      <div className="cal-head">
        <button type="button" className="icon-btn" onClick={() => onMonth(shiftMonth(month, -1))} aria-label="Previous month"><Icon name="back" /></button>
        <strong>{label}</strong>
        <button type="button" className="icon-btn flip" onClick={() => onMonth(shiftMonth(month, 1))} aria-label="Next month"><Icon name="back" /></button>
      </div>
      <div className="cal-grid">
        {WEEKDAYS.map((w) => <div key={w} className="cal-wd">{w}</div>)}
        {cells.map((d, i) => {
          if (!d) return <div key={`e${i}`} />;
          const info = byDay.get(d);
          const heat = info ? info.relevant / maxRel : 0;
          return (
            <button
              key={d}
              type="button"
              className={`cal-day ${info ? "has" : ""} ${selected === d ? "sel" : ""} ${d === today ? "today" : ""}`}
              disabled={!info}
              onClick={() => onSelect(d)}
              title={info ? `${info.new} new · ${info.revisions} revisions · ${info.relevant} relevant · ${info.digested} digested${info.has_brief ? " · brief" : ""}` : "no papers"}
            >
              <span className="cal-num">{Number(d.slice(8))}</span>
              {info && <span className="cal-count">{info.new}</span>}
              {info && <span className="cal-heat" style={{ opacity: 0.18 + 0.82 * heat, width: `${Math.max(14, heat * 100)}%` }} />}
              {info?.has_brief && <span className="cal-brief" aria-label="brief available" />}
              {info && info.starred > 0 && <span className="cal-star">★</span>}
            </button>
          );
        })}
      </div>
      <div className="cal-legend muted small">
        <span><span className="cal-heat legend" /> relevant papers</span>
        <span><span className="cal-brief legend" /> daily brief</span>
      </div>
    </div>
  );
}
