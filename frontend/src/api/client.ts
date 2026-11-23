import type {
  CalendarResponse, ContentResponse, DayResponse, GapItem, Health, JobRow, ModelsReport, PaperCard, PaperDetail, Worker,
} from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch("/api" + path, init);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (body && typeof body.detail === "string") detail = body.detail;
      else if (body?.detail) detail = JSON.stringify(body.detail);
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail);
  }
  return (res.status === 204 ? undefined : await res.json()) as T;
}

const send = (method: string, body?: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: body === undefined ? undefined : JSON.stringify(body),
});
const enc = encodeURIComponent;
const qs = (o: Record<string, string | number | boolean | undefined | null>) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(o)) if (v !== undefined && v !== null && v !== "" && v !== false) p.set(k, String(v));
  const s = p.toString();
  return s ? "?" + s : "";
};

export interface Bookmark {
  key: string;
  paper_id: string;
  kind: "paper" | "question" | "gap" | "idea";
  paper_title: string;
  title: string;
  text: string;
}

export const api = {
  bookmarks: () => request<{ items: Bookmark[] }>("/bookmarks"),
  setBookmark: (item: Bookmark, saved: boolean) => request("/bookmarks", send("PUT", { ...item, saved })),
  health: () => request<Health>("/health"),
  calendar: (month?: string) => request<CalendarResponse>("/calendar" + qs({ month })),
  day: (day: string) => request<DayResponse>(`/days/${day}`),
  papers: (o: { q?: string; topic?: string; start?: string; end?: string; min_relevance?: number; starred?: boolean; limit?: number; offset?: number; sort?: string }) =>
    request<{ total: number; papers: PaperCard[] }>("/papers" + qs(o)),
  paper: (id: string) => request<PaperDetail>(`/papers/${id}`),
  content: (id: string, version?: number) => request<ContentResponse>(`/papers/${id}/content` + qs({ version })),
  action: (id: string, action: string, note?: string) => request<PaperDetail>(`/papers/${id}/actions`, send("POST", { action, note })),
  process: (id: string, what: string, model?: string) => request<{ job_id: string }>(`/papers/${id}/process`, send("POST", { what, model })),
  add: (id: string) => request<PaperDetail>("/papers", send("POST", { id })),
  gaps: (o: { start?: string; end?: string; topic?: string; source?: string; min_relevance?: number }) =>
    request<{ start: string; end: string; items: GapItem[]; total: number }>("/gaps" + qs(o)),
  brief: (day: string, model?: string) => request<{ job_id: string }>(`/briefs/${day}` + qs({ model }), send("POST")),
  topics: () => request<{ name: string; count_30d: number }[]>("/topics"),
  jobs: (o: { status?: string; kind?: string; limit?: number } = {}) =>
    request<{ stats: Record<string, Record<string, number>>; jobs: JobRow[] }>("/jobs" + qs(o)),
  retryJob: (id: string) => request("/jobs/" + enc(id) + "/retry", send("POST")),
  cancelJob: (id: string) => request("/jobs/" + enc(id), send("DELETE")),
  retryFailed: () => request<{ retried: number }>("/jobs/retry-failed", send("POST")),
  harvests: () => request<Record<string, string | number | null>[]>("/harvests"),
  harvestNow: () => request<{ job_ids: string[] }>("/harvest", send("POST")),
  backfill: (start: string, end: string) => request<{ job_ids: string[] }>("/backfill", send("POST", { start, end })),
  sweep: () => request<Record<string, number>>("/sweep", send("POST")),
  workers: () => request<{ role: string; workers: Worker[] }>("/workers"),
  models: () => request<ModelsReport>("/models"),
  routing: (b: { groups?: Record<string, string>; premium_default?: string; cloud_enabled?: boolean }) => request("/routing", send("PUT", b)),
  usage: () => request<Record<string, string | number>[]>("/usage"),
  profile: () => request<{ text: string; description: string; interests: { name: string; weight: number; keywords: string[] }[] }>("/profile"),
  saveProfile: (text: string) => request<{ rescored: number }>("/profile", send("PUT", { text })),
  settings: () => request<Record<string, string | number | boolean | string[]>>("/settings"),
};
