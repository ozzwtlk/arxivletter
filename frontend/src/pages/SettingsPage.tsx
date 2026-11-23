import { Fragment, useEffect, useState } from "react";
import { api } from "../api/client";
import { Icon } from "../components/Icon";
import { loadModels } from "../components/ModelPicker";
import { useLoad } from "../lib/hooks";

export function SettingsPage() {
  const models = useLoad(() => loadModels(true), []);
  const profile = useLoad(() => api.profile(), []);
  const settings = useLoad(() => api.settings(), []);
  const [text, setText] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => { if (profile.data) setText(profile.data.text); }, [profile.data]);
  const r = models.data?.routing;

  const setGroup = async (gid: string, mid: string) => {
    await api.routing({ groups: { [gid]: mid } });
    models.reload();
  };
  return (
    <main className="page page-wide settings">
      <div className="page-head"><div><span className="eyebrow">Configuration</span><h1><Icon name="gear" /> Settings</h1></div></div>

      <section className="card ops-card">
        <h3>Models</h3>
        <p className="muted small">
          Token-heavy work (triage, full-text extraction, digests) runs on the local model by default. Premium reasoning models are reserved for
          AI-inferred research gaps and the daily brief, only for papers above the relevance threshold. Subscription CLIs (Claude Code, Codex) run
          with all tools disabled in an empty directory; paper text is data, never instructions.
        </p>
        {r && (
          <label className="check"><input type="checkbox" checked={r.cloud_enabled} onChange={async (e) => { await api.routing({ cloud_enabled: e.target.checked }); models.reload(); }} /> Let “Auto” use premium models (papers are public arXiv content)</label>
        )}
        <table className="tbl">
          <thead><tr><th>Task</th><th>Model</th><th>Auto resolves to</th></tr></thead>
          <tbody>
            {models.data?.groups.map((g) => (
              <tr key={g.id}>
                <td><strong>{g.label}</strong><div className="muted small">{g.help}</div></td>
                <td>
                  <select className="input" value={r?.groups[g.id] ?? "auto"} onChange={(e) => setGroup(g.id, e.target.value)}>
                    <option value="auto">Auto</option>
                    {models.data?.models.map((m) => <option key={m.id} value={m.id}>{m.label}{m.available ? "" : " (unavailable)"}</option>)}
                  </select>
                </td>
                <td className="small">{g.auto ?? "decided by the computing machine"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <h4>Available models</h4>
        <ul className="workers">
          {models.data?.models.map((m) => (
            <li key={m.id}><span className={`dot ${m.available ? "on" : ""}`} /> {m.label} <span className="muted small">{m.detail}{m.calls_today != null ? ` · ${m.calls_today} calls today${m.daily_limit ? ` / ${m.daily_limit}` : ""}` : ""}{m.machine ? ` · on ${m.machine}` : ""}</span></li>
          ))}
        </ul>
        <p className="muted small">Premium preference order: {models.data?.premium_order.join(" → ")}</p>
      </section>

      <section className="card ops-card">
        <h3>Research profile</h3>
        <p className="muted small">TOML. Drives relevance ranking (keywords, interest weights, followed authors) and is given to the models as the reader description. Saving recomputes every paper's relevance.</p>
        <textarea className="profile-edit" spellCheck={false} value={text} onChange={(e) => setText(e.target.value)} rows={28} />
        <div className="inline-form">
          <button type="button" className="btn btn-sm" onClick={async () => {
            try { const r2 = await api.saveProfile(text); setMsg(`Saved; ${r2.rescored} papers re-ranked.`); profile.reload(); }
            catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
          }}>Save profile</button>
          {msg && <span className="muted small">{msg}</span>}
        </div>
      </section>

      <section className="card ops-card">
        <h3>Processing policy</h3>
        <p className="muted small">Set with environment variables (<code>ARXIVLETTER_*</code>, see <code>.env.example</code>).</p>
        <dl className="prov">
          {settings.data && Object.entries(settings.data).map(([k, v]) => <Fragment key={k}><dt>{k.replace(/_/g, " ")}</dt><dd>{Array.isArray(v) ? v.join(", ") : String(v)}</dd></Fragment>)}
        </dl>
      </section>
    </main>
  );
}
