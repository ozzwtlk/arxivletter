// Reads a JSON array of {latex, display} from stdin; writes a JSON array of error strings (null = ok).
import katex from "katex";
let buf = "";
process.stdin.on("data", (d) => (buf += d));
process.stdin.on("end", () => {
  const items = JSON.parse(buf);
  const out = items.map(({ latex, display }) => {
    try {
      katex.renderToString(latex, { displayMode: !!display, throwOnError: true, strict: "ignore", trust: false });
      return null;
    } catch (e) {
      return String(e.message || e).slice(0, 300);
    }
  });
  process.stdout.write(JSON.stringify(out));
});
