// Renders [{latex, display, macros}] with KaTeX (as the reader does) and reports failures.
import katex from "katex";
import { readFileSync } from "node:fs";
const COMPAT = JSON.parse(readFileSync(new URL("../../frontend/src/lib/katex-compat.json", import.meta.url), "utf8"));
const items = JSON.parse(readFileSync(process.argv[2], "utf8"));
const out = items.map(({ latex, display, macros }) => {
  try { katex.renderToString(latex, { displayMode: !!display, throwOnError: true, strict: "ignore", macros: { ...COMPAT, ...macros } }); return null; }
  catch (e) { return String(e.message || e).slice(0, 160); }
});
process.stdout.write(JSON.stringify(out));
