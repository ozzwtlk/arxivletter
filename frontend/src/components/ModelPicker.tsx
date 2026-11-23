import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ModelsReport } from "../api/types";

let cache: Promise<ModelsReport> | null = null;
export const loadModels = (fresh = false) => {
  if (!cache || fresh) cache = api.models();
  return cache;
};

/** Per-request model choice ("auto" = routing settings). Unavailable models are listed but disabled. */
export function ModelPicker({ group, value, onChange }: { group: string; value: string; onChange: (v: string) => void }) {
  const [rep, setRep] = useState<ModelsReport | null>(null);
  useEffect(() => { loadModels().then(setRep).catch(() => {}); }, []);
  const auto = rep?.groups.find((g) => g.id === group)?.auto;
  return (
    <select className="input" value={value} onChange={(e) => onChange(e.target.value)} aria-label="Model">
      <option value="auto">Auto{auto ? ` (${auto})` : ""}</option>
      {rep?.models.map((m) => (
        <option key={m.id} value={m.id} disabled={!m.available}>
          {m.label}{m.available ? "" : " — unavailable"}
        </option>
      ))}
    </select>
  );
}
