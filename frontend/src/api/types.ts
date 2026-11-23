// Mirrors backend/arxivletter/api/app.py responses.

export interface PaperCard {
  id: string;
  version: number;
  title: string;
  authors: string[];
  n_authors: number;
  categories: string[];
  primary_category: string | null;
  submitted: string | null;
  day: string | null;
  comments: string | null;
  relevance: number | null;
  relevance_keyword: number | null;
  relevance_model: number | null;
  relevance_reason: string | null;
  interests: string[];
  topics: string[];
  status: string;
  digest_depth: "full" | "light" | null;
  starred: boolean;
  hidden: boolean;
  read: boolean;
  one_liner: string | null;
  paper_type: string | null;
  tldr: string | null;
  n_open_questions: number;
  top_open_question: string | null;
  n_gaps: number;
  top_gap: string | null;
  event_kind?: "new" | "revision" | null;
  event_version?: number | null;
}

export interface CalendarDay {
  day: string;
  new: number;
  revisions: number;
  top_relevance: number | null;
  relevant: number;
  digested: number;
  starred: number;
  has_brief: boolean;
}

export interface CalendarResponse {
  month: string;
  days: CalendarDay[];
  months: string[];
  latest_day: string | null;
}

export interface Brief {
  headline: string;
  overview: string;
  must_read: { id: string; why: string }[];
  themes: { title: string; summary: string; papers: string[] }[];
  cross_paper_opportunities: { title: string; description: string; papers: string[] }[];
  model: string;
  created_at?: string;
}

export interface DayResponse {
  day: string;
  papers: PaperCard[];
  brief: Brief | null;
  brief_status: string | null;
}

export interface Refd {
  text: string;
  refs: string[];
  unsupported_numbers?: string[];
}

export interface SrcLoc {
  file: string;
  line: number;
}

export interface AuthorStatement {
  id: string;
  kind: string;
  summary: string;
  quote: string;
  ref: string;
  match: "exact" | "moved" | "fuzzy" | "detected";
  section_id: string;
  section_number: string | null;
  section_title: string | null;
  anchor: string | null;
  src?: SrcLoc;
  importance?: number;
  context?: string;
  also_at?: string[];
}

export interface KeyEquation {
  id: string;
  number: string | null;
  latex: string;
  meaning: string;
  role: string;
  section_id: string | null;
}

export interface Digest {
  tldr: string;
  central_question: string;
  physical_motivation: string;
  intuition: string;
  assumptions: Refd[];
  methods: Refd[];
  key_equations: KeyEquation[];
  results: Refd[];
  limitations: Refd[];
  implications: string;
  takeaways: string[];
  reading_guide: { audience: string; prerequisites: string[]; sections_to_read: string[]; difficulty: number } | null;
  open_questions: AuthorStatement[];
  section_gists: Record<string, string>;
  key_citations: { key: string; role: string; n: number; text: string; arxiv: string | null; doi: string | null }[];
  depth: "full" | "light";
  chunks: number;
  sections_read: string[];
  skipped_sections: string[];
  source_kind: string;
  grounding: Record<string, number>;
  models: string[];
  model_classes: string[];
  escalated: boolean;
  fallback: boolean;
  calls: number;
  cached_calls: number;
  prompt_versions: string[];
  version: number;
}

export interface Grounding {
  ref: string;
  quote: string;
  match?: string;
  kind?: string;
  section_id?: string;
  section_number?: string | null;
  anchor?: string | null;
}

export interface Gap {
  title: string;
  kind: string;
  description: string;
  reasoning: string;
  grounding: Grounding[];
  builds_on: string[];
  first_steps: string;
  difficulty: number;
  impact: number;
  confidence: "low" | "medium" | "high";
  relevance_to_reader: string;
  cite_keys: string[];
  novelty_check: string;
  ungrounded: boolean;
}

export interface Idea {
  title: string;
  idea: string;
  why_promising: string;
  risks: string;
  grounding: Grounding[];
  relevance_to_reader: string;
}

export interface GapsAnalysis {
  gaps: Gap[];
  ideas: Idea[];
  assessment: { strengths: string[]; weak_points: { text: string; refs: string[] }[]; robustness: string };
  what_to_check_first: string;
  model: string;
  model_class: string;
  fallback: boolean;
  grounding: Record<string, number>;
  version: number;
}

export interface Changes {
  from_version: number;
  to_version: number;
  abstract_changed: boolean;
  sections_added: string[];
  sections_removed: string[];
  sections_changed: { section: string; similarity: number }[];
  equations_changed: string[];
  equations_added: number;
  equations_removed: number;
  length_change: number;
  references_change: number;
  author_comment: string | null;
  major: boolean;
}

export interface Analysis<T> {
  version: number;
  data: T;
  model: string | null;
  model_class: string | null;
  prompt_version: string | null;
  created_at: string;
}

export interface Triage {
  relevance: number;
  reason: string;
  topics: string[];
  paper_type: string;
  one_liner: string;
}

export interface JobRow {
  id: string;
  kind: string;
  target: string;
  status: string;
  attempts: number;
  max_attempts?: number;
  error: string | null;
  model: string | null;
  reason: string | null;
  updated_at: string;
  created_at?: string;
  worker: string | null;
  priority?: number;
  not_before?: string | null;
  result?: unknown;
}

export interface PaperDetail extends PaperCard {
  abstract: string;
  journal_ref: string | null;
  doi: string | null;
  license: string | null;
  versions: { v: number; date: string; size: string | null }[];
  note: string | null;
  relevance_detail: {
    keyword?: number;
    model?: number | null;
    boost?: number;
    followed_authors?: string[];
    interests?: { interest: string; score: number; matched: string[] }[];
    model_reason?: string;
  };
  analyses: {
    triage?: Analysis<Triage>;
    digest?: Analysis<Digest>;
    gaps?: Analysis<GapsAnalysis>;
    changes?: Analysis<Changes>;
  };
  content: {
    version: number;
    source_kind: string;
    chars: number;
    fetched_at: string;
    provenance: { url: string; source_kind: string; sha256: string; bytes: number; main_file?: string; attempts: { kind: string; url: string; result: string }[]; fetched_by?: string };
  } | null;
  jobs: JobRow[];
  links: { abs: string; pdf: string; html: string; inspire: string };
}

export interface Block {
  id: string;
  type: "paragraph" | "equation" | "figure" | "table";
  text?: string;
  latex?: string;
  number?: string | null;
  label?: string | null;
  src?: SrcLoc;
}

export interface Section {
  id: string;
  number: string | null;
  title: string;
  level: number;
  anchor: string | null;
  appendix: boolean;
  blocks: Block[];
}

export interface Reference {
  key: string;
  n: number;
  text: string;
  arxiv: string | null;
  doi: string | null;
}

export interface PaperDocument {
  source_kind: string;
  title: string;
  abstract: string;
  macros: Record<string, string>;
  sections: Section[];
  references: Reference[];
  warnings: string[];
  stats: Record<string, string | number>;
}

export interface ContentResponse {
  version: number;
  document: PaperDocument;
  provenance: Record<string, unknown>;
}

export interface GapItem {
  paper_id: string;
  title: string;
  relevance: number | null;
  day: string;
  topics: string[];
  authors: string[];
  source: "author" | "ai" | "idea";
  item: AuthorStatement | Gap | Idea;
  model?: string;
  score: number;
}

export interface ModelInfo {
  id: string;
  label: string;
  kind: string;
  available: boolean;
  detail?: string;
  calls_today?: number | null;
  daily_limit?: number | null;
  machine?: string;
}

export interface ModelsReport {
  models: ModelInfo[];
  groups: { id: string; label: string; heavy: boolean; help: string; auto: string | null }[];
  routing: { groups: Record<string, string>; premium_default: string; cloud_enabled: boolean };
  premium_order: string[];
}

export interface Worker {
  name: string;
  online: boolean;
  last_seen: string;
  info: Record<string, string>;
  kinds: string[];
  models: { id: string; label: string; available: boolean }[];
  running: { kind: string; target: string }[];
}

export interface Health {
  ok: boolean;
  role: string;
  version: string;
  papers: number;
  jobs_active: number;
  last_harvest_ok: string | null;
}
