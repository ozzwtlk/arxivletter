import { useCallback, useEffect, useRef, useState } from "react";

/** Load data; re-run when deps change; optional polling while `poll(data)` says so. */
export function useLoad<T>(fn: () => Promise<T>, deps: unknown[], poll?: (d: T) => number | false) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const reload = useCallback(async () => {
    try {
      const d = await fnRef.current();
      setData(d);
      setError(null);
      return d;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return null;
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    setLoading(true);
    const run = async () => {
      const d = await reload();
      if (!alive) return;
      const ms = d && poll ? poll(d) : false;
      if (ms) timer = window.setTimeout(run, ms);
    };
    run();
    return () => {
      alive = false;
      if (timer) window.clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return { data, error, loading, reload, setData };
}

export function fmtDay(day: string, opts: Intl.DateTimeFormatOptions = { weekday: "long", day: "numeric", month: "long", year: "numeric" }) {
  return new Date(day + "T12:00:00Z").toLocaleDateString(undefined, { ...opts, timeZone: "UTC" });
}

export function relClass(r: number | null | undefined): string {
  if (r == null) return "rel-none";
  if (r >= 8) return "rel-5";
  if (r >= 6.5) return "rel-4";
  if (r >= 5) return "rel-3";
  if (r >= 3) return "rel-2";
  return "rel-1";
}

export function ago(iso: string | null | undefined): string {
  if (!iso) return "never";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 90) return `${Math.max(0, Math.round(s))} s ago`;
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 172800) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

export const KIND_LABEL: Record<string, string> = {
  open_question: "Open question",
  future_work: "Future work",
  limitation: "Limitation",
  unresolved_issue: "Unresolved issue",
  tension: "Tension",
  caveat: "Caveat",
  implicit_assumption: "Implicit assumption",
  unexplored_regime: "Unexplored regime",
  missing_effect: "Missing effect",
  methodological: "Methodological gap",
  phenomenological_test: "Phenomenological test",
  theoretical_extension: "Theoretical extension",
  cross_field_connection: "Cross-field connection",
  tension_or_inconsistency: "Tension / inconsistency",
};
