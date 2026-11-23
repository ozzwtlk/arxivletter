import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { Icon } from "../components/Icon";
import { ago, useLoad } from "../lib/hooks";

const KINDS = ["harvest", "fetch", "triage", "digest", "gaps", "brief"];
const STATUSES = ["running", "pending", "done", "failed", "cancelled"];

export function OpsPage() {
  const health = useLoad(() => api.health(), [], () => 10000);
  const jobs = useLoad(() => api.jobs({ limit: 60 }), [], () => 5000);
  const failed = useLoad(() => api.jobs({ status: "failed", limit: 50 }), [], () => 15000);
  const workers = useLoad(() => api.workers(), [], () => 15000);
  const harvests = useLoad(() => api.harvests(), [], () => 30000);
  const usage = useLoad(() => api.usage(), [], () => 60000);
  const [msg, setMsg] = useState<string | null>(null);
  const [bf, setBf] = useState({ start: "", end: "" });
  const [addId, setAddId] = useState("");
  const run = async (f: () => Promise<unknown>, label: string) => {
    try {
      const r = await f();
      setMsg(`${label}: ${JSON.stringify(r).slice(0, 200)}`);
      jobs.reload();
    } catch (e) {
      setMsg(`${label} failed: ${e instanceof Error ? e.message : e}`);
    }
  };
  const st = jobs.data?.stats ?? {};
  const usageByModel = new Map<string, { calls: number; cached: number; pt: number; ct: number; s: number; errors: number }>();
  const today = new Date().toISOString().slice(0, 10);
  usage.data?.filter((u) => u.day === today).forEach((u) => {
    const k = `${u.model}`;
    const cur = usageByModel.get(k) ?? { calls: 0, cached: 0, pt: 0, ct: 0, s: 0, errors: 0 };
    cur.calls += Number(u.calls); cur.cached += Number(u.cached); cur.pt += Number(u.pt); cur.ct += Number(u.ct); cur.s += Number(u.seconds); cur.errors += Number(u.errors);
    usageByModel.set(k, cur);
  });

  return (
    <main className="page page-wide ops">
      <div className="page-head">
        <div><span className="eyebrow">Observability</span><h1><Icon name="server" /> Operations</h1></div>
        <div className="inline-form">
          <button type="button" className="btn btn-sm" onClick={() => run(api.harvestNow, "Harvest")}><Icon name="refresh" /> Harvest now</button>
          <button type="button" className="btn btn-quiet btn-sm" onClick={() => run(api.sweep, "Sweep")}>Sweep</button>
          <button type="button" className="btn btn-quiet btn-sm" onClick={() => run(api.retryFailed, "Retry failed")}>Retry failed</button>
          <a className="btn btn-quiet btn-sm" href="/api/metrics" target="_blank" rel="noreferrer">Prometheus metrics</a>
        </div>
      </div>
      {msg && <p className="muted small">{msg}</p>}

      <div className="ops-grid">
        <section className="card ops-card">
          <h3>Server</h3>
          {health.data ? (
            <dl className="prov">
              <dt>Role</dt><dd>{health.data.role} · v{health.data.version}</dd>
              <dt>Papers</dt><dd>{health.data.papers}</dd>
              <dt>Active jobs</dt><dd>{health.data.jobs_active}</dd>
              <dt>Last harvest</dt><dd>{ago(health.data.last_harvest_ok)}</dd>
            </dl>
          ) : <p className="error">{health.error ?? "…"}</p>}
        </section>
        <section className="card ops-card">
          <h3>Computing machines</h3>
          {workers.data?.role !== "server" && <p className="muted small">All-in-one mode: this process runs every job.</p>}
          {workers.data?.workers.length === 0 && workers.data?.role === "server" && <p className="muted small">No machine has connected yet: run <code>./arxivletter worker</code> on your PC.</p>}
          <ul className="workers">
            {workers.data?.workers.map((w) => (
              <li key={w.name}>
                <span className={`dot ${w.online ? "on" : ""}`} /> <strong>{w.name}</strong> <span className="muted small">{w.online ? "online" : `last seen ${ago(w.last_seen)}`}</span>
                <div className="small muted">{w.models.filter((m) => m.available).map((m) => m.label).join(", ") || "no models"}</div>
                {w.running.length > 0 && <div className="small">running: {w.running.map((r) => `${r.kind} ${r.target}`).join(", ")}</div>}
              </li>
            ))}
          </ul>
        </section>
        <section className="card ops-card">
          <h3>Model usage today</h3>
          <table className="tbl small">
            <thead><tr><th>Model</th><th>calls</th><th>cached</th><th>tokens in/out</th><th>GPU/API time</th></tr></thead>
            <tbody>
              {[...usageByModel.entries()].map(([m, u]) => (
                <tr key={m}><td>{m}</td><td>{u.calls}{u.errors ? ` (${u.errors} err)` : ""}</td><td>{u.cached}</td><td>{(u.pt / 1000).toFixed(0)}k / {(u.ct / 1000).toFixed(0)}k</td><td>{(u.s / 60).toFixed(0)} min</td></tr>
              ))}
            </tbody>
          </table>
        </section>
      </div>

      <section className="card ops-card">
        <h3>Queue</h3>
        <table className="tbl">
          <thead><tr><th>kind</th>{STATUSES.map((s) => <th key={s}>{s}</th>)}</tr></thead>
          <tbody>{KINDS.map((k) => <tr key={k}><td>{k}</td>{STATUSES.map((s) => <td key={s} className={st[k]?.[s] ? `c-${s}` : "muted"}>{st[k]?.[s] ?? 0}</td>)}</tr>)}</tbody>
        </table>
        <h4>Running and next</h4>
        <ul className="job-mini">
          {jobs.data?.jobs.filter((j) => j.status === "running" || j.status === "pending").slice(0, 25).map((j) => (
            <li key={j.id}>
              <span className={`badge badge-${j.status}`}>{j.status}</span> <strong>{j.kind}</strong>{" "}
              {j.kind === "harvest" || j.kind === "brief" ? j.target : <Link to={`/paper/${j.target.replace(/v\d+$/, "")}`}>{j.target}</Link>}
              <span className="muted small"> · prio {j.priority?.toFixed(0)} · {j.worker ?? ""}{j.not_before ? ` · retry after ${j.not_before.slice(11, 16)}` : ""}{j.model ? ` · ${j.model}` : ""}</span>
              <button type="button" className="link-btn small" onClick={() => run(() => api.cancelJob(j.id), "Cancel")}>cancel</button>
            </li>
          ))}
        </ul>
      </section>

      {failed.data && failed.data.jobs.length > 0 && (
        <section className="card ops-card">
          <h3>Failed jobs</h3>
          <ul className="job-mini">
            {failed.data.jobs.map((j) => (
              <li key={j.id}>
                <strong>{j.kind}</strong> {j.target} <span className="muted small">· {j.attempts} attempts · {ago(j.updated_at)}</span>
                <button type="button" className="link-btn small" onClick={() => run(() => api.retryJob(j.id), "Retry")}>retry</button>
                <details><summary className="error small">{j.error?.split("\n")[0]}</summary><pre className="err-pre">{j.error}</pre></details>
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className="ops-grid">
        <section className="card ops-card">
          <h3>Backfill</h3>
          <p className="muted small">Harvest a date range (OAI datestamps; a few days past the end are included because announcements trail submissions).</p>
          <div className="inline-form">
            <input className="input" type="date" value={bf.start} onChange={(e) => setBf({ ...bf, start: e.target.value })} />
            <input className="input" type="date" value={bf.end} onChange={(e) => setBf({ ...bf, end: e.target.value })} />
            <button type="button" className="btn btn-sm" disabled={!bf.start || !bf.end} onClick={() => run(() => api.backfill(bf.start, bf.end), "Backfill")}>Backfill</button>
          </div>
        </section>
        <section className="card ops-card">
          <h3>Add a paper</h3>
          <div className="inline-form">
            <input className="input" placeholder="2611.12345 or arXiv URL" value={addId} onChange={(e) => setAddId(e.target.value)} />
            <button type="button" className="btn btn-sm" disabled={!addId} onClick={() => run(() => api.add(addId).then((p) => p.id), "Added")}>Add</button>
          </div>
        </section>
      </div>

      <section className="card ops-card">
        <h3>Harvest history</h3>
        <table className="tbl small">
          <thead><tr><th>set</th><th>window</th><th>status</th><th>records</th><th>new</th><th>new versions</th><th>started</th><th>error</th></tr></thead>
          <tbody>{harvests.data?.slice(0, 20).map((h) => (
            <tr key={String(h.id)}><td>{h.set_spec}</td><td>{h.from_day} → {h.until_day}</td><td className={`c-${h.status}`}>{h.status}</td><td>{h.records}</td><td>{h.new_papers}</td><td>{h.new_versions}</td><td>{ago(String(h.started_at))}</td><td className="error">{h.error}</td></tr>
          ))}</tbody>
        </table>
      </section>
    </main>
  );
}
