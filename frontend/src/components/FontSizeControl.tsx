/* The [A−] 14 [A+] control for the Markdown reading page.
 *
 * Only the standalone reading page mounts this. The embedded surfaces — the
 * side panel, the zoom overlay — render at their own fixed density default and
 * carry no controls, so a narrow panel can never be handed a size chosen for a
 * full-width document. See `markdownReader.ts` for the persistence, and
 * `markdownFont.ts` for why the sizes are a fixed ladder.
 *
 * No keyboard shortcut: Cmd/Ctrl +/- stays the browser's.
 */

import { FONT_STEPS, clampFontIndex } from "../markdownFont";
import { Stepper } from "./Stepper";

export type FontSizeControlProps = {
  index: number;
  onChange: (index: number) => void;
  /** Where a click on the label resets to. */
  defaultIndex: number;
};

export function FontSizeControl({
  index,
  onChange,
  defaultIndex,
}: FontSizeControlProps) {
  const current = clampFontIndex(index);

  return (
    <Stepper
      label={String(FONT_STEPS[current])}
      decGlyph="A−"
      incGlyph="A+"
      onDec={() => onChange(clampFontIndex(current - 1))}
      onInc={() => onChange(clampFontIndex(current + 1))}
      onReset={() => onChange(defaultIndex)}
      atMin={current === 0}
      atMax={current === FONT_STEPS.length - 1}
      decTitle="减小字号"
      incTitle="增大字号"
      resetTitle="重置字号"
    />
  );
}
