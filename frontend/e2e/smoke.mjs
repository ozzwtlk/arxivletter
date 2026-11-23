// End-to-end smoke test against a running arXivletter (all-in-one or server) with harvested papers.
//   node e2e/smoke.mjs [http://127.0.0.1:8010] [screenshot dir]
// Browser: PLAYWRIGHT_CHROMIUM=<path> or the playwright cache (npx playwright-core install chromium).
import { chromium } from "playwright-core";
import { mkdirSync } from "node:fs";

const BASE = process.argv[2] ?? "http://127.0.0.1:8010";
const SHOTS = process.argv[3] ?? "/tmp/arxivletter-shots";
mkdirSync(SHOTS, { recursive: true });
const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  — " + detail : ""}`);
};
const api = async (p) => (await fetch(BASE + "/api" + p)).json();

const cal = await api("/calendar");
const day = cal.latest_day;
const dayData = await api(`/days/${day}`);
const digested = (await api("/papers?status=digested&limit=50")).papers;
const withGaps = digested.find((p) => p.n_gaps > 0) ?? digested[0];

const browser = await chromium.launch(process.env.PLAYWRIGHT_CHROMIUM ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM } : {});
const page = await browser.newPage({ viewport: { width: 1360, height: 1800 } });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => m.type() === "error" && !/Failed to load resource.*404/.test(m.text()) && errors.push(m.text()));

await page.goto(BASE + "/", { waitUntil: "networkidle" });
await page.waitForSelector(".paper-card");
check("calendar home shows the latest day", (await page.textContent(".day-head h1")).length > 5);
check("day lists papers", (await page.$$(".paper-card")).length >= Math.min(5, dayData.papers.length));
check("calendar grid has days with papers", (await page.$$(".cal-day.has")).length >= 1);
await page.screenshot({ path: `${SHOTS}/1-home.png` });

const chip = await page.$(".topics-filter .chip");
if (chip) {
  const before = (await page.$$(".paper-card")).length;
  await chip.click();
  await page.waitForTimeout(200);
  const after = (await page.$$(".paper-card")).length;
  check("topic filter narrows the list", after <= before && after > 0, `${before} → ${after}`);
  await chip.click();
}

if (withGaps) {
  await page.goto(`${BASE}/paper/${withGaps.id}`, { waitUntil: "networkidle" });
  await page.waitForSelector(".paper-head h1");
  check("paper view: TL;DR", !!(await page.$(".tldr")));
  check("paper view: author open problems with verified quotes", (await page.$$(".oq blockquote.quote")).length > 0);
  check("paper view: key equations rendered by KaTeX", (await page.$$(".key-eq .katex-display")).length > 0);
  await page.screenshot({ path: `${SHOTS}/2-paper-top.png` });
  const chipBtn = await page.$("#digest .refd .ref-chip");
  if (chipBtn) {
    await chipBtn.click();
    await page.waitForSelector(".drawer .block-view", { timeout: 5000 });
    check("source chip opens the cited paragraph", !!(await page.$(".drawer .block-view .block-body")));
    await page.screenshot({ path: `${SHOTS}/3-source-drawer.png` });
    await page.keyboard.press("Escape");
  }
  await page.click("text=Show parsed text");
  await page.waitForSelector(".fulltext .ft-blk");
  check("full text renders", (await page.$$(".fulltext .ft-blk")).length > 10);
  const bad = await page.$$eval(".fulltext .math-error", (els) => els.length);
  const all = await page.$$eval(".fulltext .katex, .fulltext .math-error", (els) => els.length);
  check("full-text math renders", bad / Math.max(1, all) < 0.05, `${bad} of ${all} formulas fall back to raw TeX`);
  await page.evaluate(() => document.getElementById("gaps")?.scrollIntoView());
  await page.screenshot({ path: `${SHOTS}/4-paper-gaps.png` });
}

await page.goto(`${BASE}/gaps?`, { waitUntil: "networkidle" });
await page.waitForTimeout(800);
check("research-gap feed lists items", (await page.$$(".gi")).length > 0);
await page.screenshot({ path: `${SHOTS}/5-gaps.png` });

await page.goto(`${BASE}/search?q=dark+matter`, { waitUntil: "networkidle" });
await page.waitForTimeout(500);
check("search finds papers", (await page.$$(".paper-card")).length > 0);

await page.goto(`${BASE}/ops`, { waitUntil: "networkidle" });
check("operations page shows the queue", !!(await page.$(".tbl")));
await page.screenshot({ path: `${SHOTS}/6-ops.png` });
await page.goto(`${BASE}/settings`, { waitUntil: "networkidle" });
check("settings page shows routing", !!(await page.$(".profile-edit")));

await page.emulateMedia({ colorScheme: "dark" });
await page.goto(BASE + "/", { waitUntil: "networkidle" });
await page.waitForSelector(".paper-card");
await page.screenshot({ path: `${SHOTS}/7-home-dark.png` });

check("no console errors", errors.length === 0, errors.slice(0, 3).join(" | "));
await browser.close();
const failed = results.filter((r) => !r.ok).length;
console.log(`\n${results.length - failed}/${results.length} passed; screenshots in ${SHOTS}`);
process.exit(failed ? 1 : 0);
