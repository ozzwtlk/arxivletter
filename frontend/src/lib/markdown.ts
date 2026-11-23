/**
 * Math-protecting Markdown renderer.
 *
 * 1. Extract math ($..$, $$..$$, \(..\), \[..\]) and replace each with an opaque placeholder
 *    built from Unicode private-use characters, which markdown-it passes through untouched.
 * 2. Render the remaining Markdown (raw HTML disabled: PDF content is untrusted).
 * 3. Render each math segment with KaTeX (throwOnError:false, plus try/catch as a last resort)
 *    and substitute it back.
 * Results are memoized per input string (caches are cleared when the paper context — macros, citations — changes).
 * Adapted from AXIOM. Additions: the paper's own \newcommand macros for KaTeX, and `[cite:key1,key2]` markers
 * rendered as numbered reference links.
 */
import katex from "katex";
import MarkdownIt from "markdown-it";
import COMPAT from "./katex-compat.json";

const md = new MarkdownIt({ html: false, linkify: false, typographer: false, breaks: false });

interface PaperContext {
  macros: Record<string, string>;
  cites: Record<string, number>; // citation key -> reference number
  id: string;
}
const ctx: PaperContext = { macros: {}, cites: {}, id: "" };

/** Set the paper whose macros / bibliography are used for rendering (clears the caches when it changes). */
export function setPaperContext(id: string, macros: Record<string, string> | undefined, cites: Record<string, number> | undefined) {
  if (ctx.id === id) return;
  ctx.id = id;
  ctx.macros = macros ?? {};
  ctx.cites = cites ?? {};
  mathCache.clear();
  blockCache.clear();
  inlineCache.clear();
}

const REF_RE = /\[(?:cite:([^\]]*)|((?:[PEFTQS]\d+)(?:\s*[,;–-]\s*(?:[PEFTQS]\d+))*))\]/g;
/** Only expand references and math in text tokens, never inside HTML attributes or code. */
md.renderer.rules.text = (tokens, idx, _options, env) => {
  const text = nbsp(tokens[idx].content);
  let inLink = false;
  for (let i = idx - 1; i >= 0; i--) {
    if (tokens[i].type === "link_close") break;
    if (tokens[i].type === "link_open") { inLink = true; break; }
  }
  const math = (env as { math?: MathSegment[] } | undefined)?.math ?? [];
  if (inLink) return restore(escapeHtml(text), math);
  let html = "";
  let last = 0;
  for (const m of text.matchAll(REF_RE)) {
    html += restore(escapeHtml(text.slice(last, m.index)), math);
    last = m.index + m[0].length;
    const [, keys, ids] = m;
    if (ids) {
      html += "[" + ids.split(/\s*([,;–-])\s*/).map((t) =>
        /^[PEFTQS]\d+$/.test(t) ? `<a class="blockref" href="#" data-ref="${t}">${t}</a>` : t,
      ).join("") + "]";
    } else {
      const ks = keys.split(",").map((k) => k.trim()).filter(Boolean);
      const nums = ks.filter((k) => Object.hasOwn(ctx.cites, k)).map((k) => ctx.cites[k]);
      const label = nums.length ? escapeHtml(nums.join(", ")) : ks.length + (ks.length === 1 ? " ref" : " refs");
      html += `<a class="cite" href="#ref-${escapeHtml(ks[0] ?? "")}" data-keys="${escapeHtml(ks.join(","))}">[${label}]</a>`;
    }
  }
  return html + restore(escapeHtml(text.slice(last)), math);
};

const PH_OPEN = "";
const PH_CLOSE = "";
const PH_RE = /(\d+)/g;

export interface MathSegment {
  tex: string;
  display: boolean;
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}

/** Visible fallback for LaTeX that KaTeX cannot handle. Styled by `.math-error` (red border). */
function fallback(tex: string, display: boolean, err: string): string {
  const src = display ? `$$${tex}$$` : `$${tex}$`;
  return `<code class="math-error${display ? " math-error-display" : ""}" title="${escapeHtml(err)}">${escapeHtml(src)}</code>`;
}

const mathCache = new Map<string, string>();
const MAX_CACHE = 4000;

function remember(cache: Map<string, string>, key: string, value: string) {
  if (cache.size >= MAX_CACHE) {
    // drop the oldest quarter (Map preserves insertion order)
    let n = Math.floor(MAX_CACHE / 4);
    for (const k of cache.keys()) {
      cache.delete(k);
      if (--n <= 0) break;
    }
  }
  cache.set(key, value);
}

/** Render one LaTeX string with KaTeX. Never throws. Memoized. */
export function renderMath(tex: string, display: boolean): string {
  const key = (display ? "D:" : "I:") + tex;
  const hit = mathCache.get(key);
  if (hit !== undefined) return hit;
  let out: string;
  try {
    out = katex.renderToString(tex, {
      displayMode: display,
      macros: { ...(COMPAT as Record<string, string>), ...ctx.macros },
      throwOnError: false,
      strict: "ignore",
      trust: false,
      output: "htmlAndMathml",
    });
    // With throwOnError:false KaTeX emits <span class="katex-error" ...>; replace it with our
    // more explicit fallback so the raw source is clearly marked.
    if (out.includes('class="katex-error"')) {
      const m = /title="([^"]*)"/.exec(out);
      out = fallback(tex, display, m ? m[1].replace(/&quot;/g, '"').replace(/&amp;/g, "&") : "LaTeX error");
    }
  } catch (e) {
    out = fallback(tex, display, e instanceof Error ? e.message : String(e));
  }
  remember(mathCache, key, out);
  return out;
}

/**
 * Split `src` into text-with-placeholders and math segments.
 * Backtick code spans/fences are skipped (math inside code stays literal).
 */
export function extractMath(src: string): { text: string; math: MathSegment[] } {
  const math: MathSegment[] = [];
  let out = "";
  let i = 0;
  const n = src.length;
  const push = (tex: string, display: boolean) => {
    out += PH_OPEN + math.length + PH_CLOSE;
    math.push({ tex, display });
  };

  while (i < n) {
    const c = src[i];

    // Code spans / fences: copy verbatim up to the matching backtick run.
    if (c === "`") {
      let j = i;
      while (j < n && src[j] === "`") j++;
      const run = src.slice(i, j);
      const end = src.indexOf(run, j);
      if (end === -1) {
        out += run;
        i = j;
      } else {
        out += src.slice(i, end + run.length);
        i = end + run.length;
      }
      continue;
    }

    if (c === "\\") {
      const d = src[i + 1];
      if (d === "(" || d === "[") {
        const close = d === "(" ? "\\)" : "\\]";
        const end = src.indexOf(close, i + 2);
        if (end !== -1) {
          push(src.slice(i + 2, end), d === "[");
          i = end + 2;
          continue;
        }
      }
      // Keep other escapes (incl. \$) for markdown-it to handle.
      out += c + (d ?? "");
      i += 2;
      continue;
    }

    if (c === "$") {
      if (src[i + 1] === "$") {
        const end = findClosingDollar(src, i + 2, true);
        if (end !== -1) {
          push(src.slice(i + 2, end), true);
          i = end + 2;
          continue;
        }
        out += "$$";
        i += 2;
        continue;
      }
      // Inline: opening $ must not be followed by whitespace.
      const next = src[i + 1];
      if (next !== undefined && !/\s/.test(next)) {
        const end = findClosingDollar(src, i + 1, false);
        if (end !== -1) {
          push(src.slice(i + 1, end), false);
          i = end + 1;
          continue;
        }
      }
      out += "$";
      i += 1;
      continue;
    }

    out += c;
    i += 1;
  }
  return { text: out, math };
}

function findClosingDollar(src: string, from: number, display: boolean): number {
  for (let j = from; j < src.length; j++) {
    const ch = src[j];
    if (ch === "\\") {
      j++; // skip escaped char (e.g. \$ inside math)
      continue;
    }
    if (!display && ch === "\n" && src[j + 1] === "\n") return -1; // inline math cannot span paragraphs
    if (ch === "$") {
      if (display) {
        if (src[j + 1] === "$") return j;
      } else {
        if (j === from) return -1;
        const prev = src[j - 1];
        const after = src[j + 1];
        if (/\s/.test(prev)) continue;
        if (after !== undefined && /\d/.test(after)) continue; // "$5 and $6" heuristics
        return j;
      }
    }
  }
  return -1;
}

// Inline math plus the punctuation right after it ("point $p$.") must not break across lines.
const PH_PUNCT_RE = new RegExp(PH_RE.source + "([.,;:!?)\\]]*)", "g");

function restore(html: string, math: MathSegment[]): string {
  return html.replace(PH_PUNCT_RE, (_m, k: string, punct: string) => {
    const seg = math[Number(k)];
    if (!seg) return punct;
    const out = renderMath(seg.tex, seg.display);
    return punct && !seg.display ? `<span class="math-nowrap">${out}${punct}</span>` : out + punct;
  });
}

const blockCache = new Map<string, string>();
const inlineCache = new Map<string, string>();
// rendered HTML depends on the cross-reference index: start over when it changes


/** LaTeX's non-breaking space "~" between words (1.2~TeV) → U+00A0 outside math. */
function nbsp(text: string): string {
  return text.replace(/(?<=[^\s~])~(?=[^\s~])/g, "\u00a0");
}

/** Render a Markdown string (block-level) with math. Never throws. Memoized. */
export function renderMarkdown(src: string | null | undefined): string {
  if (!src) return "";
  const hit = blockCache.get(src);
  if (hit !== undefined) return hit;
  let html: string;
  try {
    const { text, math } = extractMath(src);
    html = md.render(text, { math });
    // A paragraph consisting solely of display math: unwrap so the block is not inside <p>.
    html = html.replace(/<p>\s*(<span class="katex-display">[\s\S]*?<\/span>)\s*<\/p>/g, (m, inner: string) =>
      countSpans(inner) ? `<div class="math-block">${inner}</div>` : m,
    );
  } catch (e) {
    html = `<pre class="math-error">${escapeHtml(src)}</pre>`;
    console.error("renderMarkdown failed", e);
  }
  remember(blockCache, src, html);
  return html;
}

// Make sure the unwrap regex matched a balanced span (non-greedy match could stop early).
function countSpans(s: string): boolean {
  const open = (s.match(/<span\b/g) || []).length;
  const close = (s.match(/<\/span>/g) || []).length;
  return open === close;
}

/** Render inline Markdown (no <p> wrapper), e.g. for titles. Never throws. Memoized. */
export function renderInline(src: string | null | undefined): string {
  if (!src) return "";
  const hit = inlineCache.get(src);
  if (hit !== undefined) return hit;
  let html: string;
  try {
    const { text, math } = extractMath(src);
    html = md.renderInline(text, { math });
  } catch {
    html = escapeHtml(src);
  }
  remember(inlineCache, src, html);
  return html;
}

/** For tests / diagnostics. */
export function _cacheSizes() {
  return { math: mathCache.size, block: blockCache.size, inline: inlineCache.size };
}

/** Shorten Markdown to about `n` characters without cutting inside a $...$ formula or a word. */
export function excerpt(src: string, n: number): string {
  if (src.length <= n) return src;
  let cut = src.slice(0, n);
  const dollars = (cut.match(/(?<!\\)\$/g) ?? []).length;
  if (dollars % 2 === 1) cut = cut.slice(0, cut.search(/(?<!\\)\$(?![\s\S]*(?<!\\)\$)/)); // drop the open formula
  const sp = cut.lastIndexOf(" ");
  if (sp > n * 0.6) cut = cut.slice(0, sp);
  return cut.replace(/[\s,;:]+$/, "") + "…";
}
