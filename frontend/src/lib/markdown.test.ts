import { describe, expect, it } from "vitest";
import { renderInline, renderMarkdown, setPaperContext } from "./markdown";

describe("paper-aware markdown", () => {
  it("links [cite:…] to numbered references", () => {
    setPaperContext("p1", {}, { "Gies:2005": 12, "Schwinger:1951nm": 3 });
    const html = renderMarkdown("Known since [cite:Schwinger:1951nm,Gies:2005].");
    expect(html).toContain('class="cite"');
    expect(html).toContain("[3, 12]");
    expect(html).toContain('data-keys="Schwinger:1951nm,Gies:2005"');
  });

  it("renders the paper's own macros and the compatibility set", () => {
    setPaperContext("p2", { "\\mchi": "m_\\chi" }, {});
    const html = renderInline("mass $\\mchi$ and $\\slashed{p}$ and $\\Tr F^2$");
    expect(html).not.toContain("math-error");
    expect(html).toContain("katex");
  });

  it("switching papers clears cached renders", () => {
    setPaperContext("p3", { "\\X": "a" }, {});
    const a = renderInline("$\\X$");
    setPaperContext("p4", { "\\X": "b" }, {});
    expect(renderInline("$\\X$")).not.toEqual(a);
  });

  it("never renders raw HTML from paper text", () => {
    setPaperContext("p5", {}, {});
    expect(renderMarkdown("<script>alert(1)</script>")).not.toContain("<script>");
  });
});

describe("block references in model prose", () => {
  it("turns [P73], [E23]-[E26] and [Q1] into source links", () => {
    setPaperContext("p6", {}, {});
    const html = renderInline("as stated [P73] and in [E23]-[E26]; see [Q1]");
    expect(html).toContain('data-ref="P73"');
    expect(html).toContain('data-ref="E26"');
    expect(html).toContain('data-ref="Q1"');
  });

  it("keeps references in link titles, link labels and code literal", () => {
    const html = renderInline('[see P12 [P12]](https://arxiv.org/ "[E23] [cite:key]") and `[Q1]`');
    expect(html).toContain('title="[E23] [cite:key]"');
    expect(html).toContain('see P12 [P12]</a>');
    expect(html).toContain('<code>[Q1]</code>');
    expect(html).not.toMatch(/class="(?:blockref|cite)"/);
  });
});
