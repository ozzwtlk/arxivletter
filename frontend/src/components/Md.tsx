import { memo } from "react";
import { renderInline, renderMarkdown, renderMath } from "../lib/markdown";

/** Block Markdown + math (KaTeX with the paper's macros), memoized. */
export const Md = memo(function Md({ src, className }: { src: string | null | undefined; className?: string }) {
  return <div className={className ? `md ${className}` : "md"} dangerouslySetInnerHTML={{ __html: renderMarkdown(src) }} />;
});

export const MdInline = memo(function MdInline({ src, className }: { src: string | null | undefined; className?: string }) {
  return <span className={className} dangerouslySetInnerHTML={{ __html: renderInline(src) }} />;
});

/** Display equation with its number, e.g. (3.4). The LaTeX is always the paper's own. */
export const Equation = memo(function Equation({ latex, number }: { latex: string; number?: string | null }) {
  return (
    <div className="equation">
      <div className="equation-math" dangerouslySetInnerHTML={{ __html: renderMath(latex, true) }} />
      {number && <span className="equation-number">({number})</span>}
    </div>
  );
});
