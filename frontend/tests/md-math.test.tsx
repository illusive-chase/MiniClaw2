import assert from "node:assert/strict";
import { renderToStaticMarkup } from "react-dom/server.browser";

import { MarkdownView } from "../src/components/MarkdownView";

/* Rendered through MarkdownView itself, so the plugin order (KaTeX before
 * highlight.js) is covered along with the tokenizer. */
function render(text: string): string {
  return renderToStaticMarkup(<MarkdownView text={text} />);
}

function inlineCount(html: string): number {
  return html.split('<span class="katex">').length - 1;
}

function displayCount(html: string): number {
  return html.split('<span class="katex-display">').length - 1;
}

function assertPlain(text: string, literal: string) {
  const html = render(text);
  assert.equal(inlineCount(html), 0, `${text} should not render math: ${html}`);
  assert.ok(html.includes(literal), `${text} should keep ${literal}: ${html}`);
}

// Ordinary inline math, including adjacent punctuation and letters.
for (const text of ["Inline $E=mc^2$ here.", "$x$, then", "a$b$c", "**$x$** bold"]) {
  const html = render(text);
  assert.equal(inlineCount(html), 1, `${text}: ${html}`);
  assert.equal(displayCount(html), 0, `${text}: ${html}`);
}

// Pandoc's single-dollar rule keeps agent prose dollars as text.
assertPlain("It costs $5 and $10 total.", "$5 and $10");
assertPlain(
  "Set $MINICLAW_HOME and $MINICLAW_CONTEXT_HOME first.",
  "$MINICLAW_HOME and $MINICLAW_CONTEXT_HOME",
);
assertPlain("$ x $ is spaced", "$ x $");
assertPlain("$x $ has a space before the closer", "$x $");
assertPlain("$x$5 has a digit after the closer", "$x$5");
assertPlain("Escaped \\$5 and \\$10", "$5 and $10");
// A rejected `$` must not swallow a code span that follows it.
{
  const html = render("costs $5 and `code` and $10");
  assert.equal(inlineCount(html), 0, html);
  assert.ok(html.includes("<code>code</code>"), html);
}
// Code keeps its dollars, whatever surrounds it.
assert.ok(render("`$HOME` and `$x$`").includes("<code>$x$</code>"));

// A rejected span leaves later valid math alone.
assert.equal(inlineCount(render("price $5, formula $x$ end")), 1);

// `\$` inside math is TeX's dollar sign, not the closer.
{
  const html = render("Cost $\\$5$ only");
  assert.equal(inlineCount(html), 1, html);
  assert.ok(!html.includes("only$"), html);
}

// `$$` is display math wherever it sits, with no Pandoc restriction.
for (const text of [
  "$$\n\\int_0^1 x\\,dx\n$$",
  "结果是 $$a^2+b^2$$。",
  "$$ E = mc^2 $$",
  "```math\nx^2\n```",
]) {
  const html = render(text);
  assert.equal(displayCount(html), 1, `${text}: ${html}`);
  assert.ok(!html.includes("<pre"), `${text} should not stay a code block: ${html}`);
}

// Inside lists and tables too.
assert.equal(displayCount(render("- item\n\n  $$\n  y\n  $$")), 1);
assert.equal(inlineCount(render("| a | b |\n|---|---|\n| $x$ | $5 |")), 1);

// Bad TeX degrades to a visible error instead of throwing, in a theme colour.
{
  const html = render("$\\frac{1}$");
  assert.ok(html.includes("katex-error"), html);
  assert.ok(html.includes("rgb(var(--state-error))"), html);
}

// Ordinary fenced code is still highlighted.
assert.ok(render("```python\nx = 1\n```").includes("hljs"));

console.log("md-math tests passed");
