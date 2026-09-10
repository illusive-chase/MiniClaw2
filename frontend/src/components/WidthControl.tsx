/* The [⇥⇤] 84ch [⇤⇥] control for the reading page's column width.
 *
 * What the reader is actually adjusting is the side margins, but the value it
 * sets is the column cap, because that is the number that means something: the
 * measure of the text. Narrow rung, wide margins. The arrows therefore point
 * the way the *margins* move — inward glyph narrows the column and grows the
 * margins — since that is the change the eye sees on the page.
 *
 * Lives beside FontSizeControl in the reading page header and shares its
 * chrome through `Stepper`; only the reading page has these controls.
 */

import { WIDTH_STEPS, clampWidthIndex } from "../markdownReader";
import { Stepper } from "./Stepper";

export type WidthControlProps = {
  index: number;
  onChange: (index: number) => void;
  /** Where a click on the label resets to. */
  defaultIndex: number;
};

export function WidthControl({
  index,
  onChange,
  defaultIndex,
}: WidthControlProps) {
  const current = clampWidthIndex(index);

  return (
    <Stepper
      label={`${WIDTH_STEPS[current]}ch`}
      decGlyph="⇥⇤"
      incGlyph="⇤⇥"
      onDec={() => onChange(clampWidthIndex(current - 1))}
      onInc={() => onChange(clampWidthIndex(current + 1))}
      onReset={() => onChange(defaultIndex)}
      atMin={current === 0}
      atMax={current === WIDTH_STEPS.length - 1}
      decTitle="增大页边距"
      incTitle="减小页边距"
      resetTitle="重置页边距"
      labelMinWidth="2.9rem"
    />
  );
}
