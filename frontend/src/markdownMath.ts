/* `$…$` and `$$…$$` math for MarkdownView.
 *
 * `remark-math` would do this in one line, but its inline rule pairs any two
 * dollars on a line, and agent prose is full of dollars that are not math:
 * "costs $5 and $10", "set $MINICLAW_HOME and $MINICLAW_CONTEXT_HOME". Both
 * render the stretch between the dollars as an italic formula. So this keeps
 * its parts — the `$$` block construct from `micromark-extension-math`, the
 * tree builder from `mdast-util-math` — and swaps in an inline tokenizer that
 * follows Pandoc's `tex_math_dollars` rule for a single `$`:
 *
 * - the opening `$` must be followed by a non-space;
 * - a `$` that follows a space abandons the span (it cannot close it);
 * - the closing `$` must not be followed by a digit.
 *
 * `$$` is exempt from all three, and also renders as display math wherever it
 * appears, as in LaTeX and Pandoc; `remark-math` only does that when the
 * fences sit on their own lines. Inside either, `\$` is TeX's dollar sign, so
 * it never closes the span — also as in Pandoc.
 */

import { mathFromMarkdown } from "mdast-util-math";
import type { InlineMath } from "mdast-util-math";
import { math } from "micromark-extension-math";
import {
  asciiDigit,
  markdownLineEnding,
  markdownLineEndingOrSpace,
} from "micromark-util-character";
import type {
  Code,
  Construct,
  Effects,
  Extension,
  State,
  Token,
  TokenizeContext,
} from "micromark-util-types";
import type { Options as KatexOptions } from "rehype-katex";
import type { Processor } from "unified";

const DOLLAR = 36;
const SPACE = 32;
const BACKSLASH = 92;

const upstream = math();
/* Reused for its resolver (padding and data merging) and its `previous`
 * check (an unescaped `$` cannot start math right after another `$`). */
const upstreamText = upstream.text![DOLLAR] as Construct;

const mathText: Construct = {
  ...upstreamText,
  tokenize: tokenizeMathText,
};

const syntax: Extension = {
  flow: upstream.flow,
  text: { [DOLLAR]: mathText },
};

/* Mirrors `micromark-extension-math`'s text tokenizer. The differences are
 * the three Pandoc checks, each for a single `$` only, and `escaped`. */
function tokenizeMathText(
  this: TokenizeContext,
  effects: Effects,
  ok: State,
  nok: State,
): State {
  const self = this;
  let sizeOpen = 0;
  let size = 0;
  let token: Token;
  return start;

  function start(code: Code): State | undefined {
    effects.enter("mathText");
    effects.enter("mathTextSequence");
    return sequenceOpen(code);
  }

  function sequenceOpen(code: Code): State | undefined {
    if (code === DOLLAR) {
      effects.consume(code);
      sizeOpen++;
      return sequenceOpen;
    }
    if (sizeOpen === 1 && (code === null || markdownLineEndingOrSpace(code))) {
      return nok(code);
    }
    effects.exit("mathTextSequence");
    return between(code);
  }

  function between(code: Code): State | undefined {
    if (code === null) return nok(code);
    if (code === DOLLAR) {
      if (sizeOpen === 1 && markdownLineEndingOrSpace(self.previous)) {
        return nok(code);
      }
      token = effects.enter("mathTextSequence");
      size = 0;
      return sequenceClose(code);
    }
    if (code === SPACE) {
      effects.enter("space");
      effects.consume(code);
      effects.exit("space");
      return between;
    }
    if (markdownLineEnding(code)) {
      effects.enter("lineEnding");
      effects.consume(code);
      effects.exit("lineEnding");
      return between;
    }
    effects.enter("mathTextData");
    return data(code);
  }

  function data(code: Code): State | undefined {
    if (
      code === null ||
      code === SPACE ||
      code === DOLLAR ||
      markdownLineEnding(code)
    ) {
      effects.exit("mathTextData");
      return between(code);
    }
    effects.consume(code);
    return code === BACKSLASH ? escaped : data;
  }

  /* `\\` is consumed as a pair, so `$a\\$` still closes after it. */
  function escaped(code: Code): State | undefined {
    if (code === DOLLAR || code === BACKSLASH) {
      effects.consume(code);
      return data;
    }
    return data(code);
  }

  function sequenceClose(code: Code): State | undefined {
    if (code === DOLLAR) {
      effects.consume(code);
      size++;
      return sequenceClose;
    }
    if (size === sizeOpen) {
      if (sizeOpen === 1 && asciiDigit(code)) return nok(code);
      effects.exit("mathTextSequence");
      effects.exit("mathText");
      return ok(code);
    }
    // A run of a different length is content, as in `$a$$b$`.
    token.type = "mathTextData";
    return data(code);
  }
}

const upstreamTree = mathFromMarkdown();
const enterMathText = upstreamTree.enter!.mathText!;

const enterDisplayAwareMathText: typeof enterMathText = function (token) {
  enterMathText.call(this, token);
  if (!this.sliceSerialize(token).startsWith("$$")) return;
  // `enterMathText` opened the node, then a buffer for its value.
  const node = this.stack[this.stack.length - 2] as InlineMath;
  node.data = {
    ...node.data,
    // `math-display` is what tells rehype-katex to typeset in display mode.
    hProperties: { className: ["language-math", "math-display"] },
  };
};

const tree: typeof upstreamTree = {
  ...upstreamTree,
  enter: { ...upstreamTree.enter, mathText: enterDisplayAwareMathText },
};

type ParserData = {
  micromarkExtensions?: Extension[];
  fromMarkdownExtensions?: (typeof tree)[];
};

export function remarkMath(this: Processor): undefined {
  const data = this.data() as ParserData;
  (data.micromarkExtensions ??= []).push(syntax);
  (data.fromMarkdownExtensions ??= []).push(tree);
}

export const katexOptions: KatexOptions = {
  // An inline style, so a theme variable keeps the colour right in dark mode.
  errorColor: "rgb(var(--state-error))",
  // Agent prose puts CJK text inside math; KaTeX renders it, so don't warn.
  strict: "ignore",
};
