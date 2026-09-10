import assert from "node:assert/strict";

import {
    FONT_STEPS,
    clampFontIndex,
    defaultFontIndex,
    fontPxAt,
} from "../src/markdownFont";

// The ladder is ordered and has the documented defaults on it.
{
    for (let i = 1; i < FONT_STEPS.length; i += 1) {
        assert.ok(FONT_STEPS[i] > FONT_STEPS[i - 1], "steps must increase");
    }
    assert.equal(FONT_STEPS[defaultFontIndex("panel")], 13);
    assert.equal(FONT_STEPS[defaultFontIndex("overlay")], 14);
    assert.equal(FONT_STEPS[defaultFontIndex("page")], 16);
}

// clampFontIndex: clamps, never wraps; rounds; survives garbage.
{
    assert.equal(clampFontIndex(-3), 0);
    assert.equal(clampFontIndex(999), FONT_STEPS.length - 1);
    assert.equal(clampFontIndex(2.4), 2);
    assert.equal(clampFontIndex(Number.NaN), 0);
}

// fontPxAt maps through the clamp.
{
    assert.equal(fontPxAt(0), FONT_STEPS[0]);
    assert.equal(fontPxAt(999), FONT_STEPS[FONT_STEPS.length - 1]);
}

// Stepping from a default lands on adjacent rungs and stops at the ends.
{
    const start = defaultFontIndex("panel");
    assert.equal(fontPxAt(start + 1), FONT_STEPS[start + 1]);
    assert.equal(fontPxAt(start - 1), FONT_STEPS[start - 1]);
    // Repeated increments saturate rather than overflow.
    let idx = 0;
    for (let i = 0; i < 100; i += 1) idx = clampFontIndex(idx + 1);
    assert.equal(idx, FONT_STEPS.length - 1);
}

console.log("md-font: ok");
