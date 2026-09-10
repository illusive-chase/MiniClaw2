import assert from "node:assert/strict";

import { FONT_STEPS, defaultFontIndex } from "../src/markdownFont";
import {
    DEFAULT_WIDTH_CH,
    READER_PREFS_STORAGE_KEY,
    WIDTH_STEPS,
    clampWidthIndex,
    defaultReaderPrefs,
    defaultWidthIndex,
    normalizeReaderPrefs,
    readReaderPrefs,
    widthChAt,
    writeReaderPrefs,
} from "../src/markdownReader";

/* A localStorage stand-in. `throwing` reproduces a private window or blocked
 * site data, where even touching the property throws. */
function installStorage(options: { throwing?: boolean } = {}) {
    const map = new Map<string, string>();
    const store = {
        getItem: (k: string) => {
            if (options.throwing) throw new Error("blocked");
            return map.has(k) ? (map.get(k) as string) : null;
        },
        setItem: (k: string, v: string) => {
            if (options.throwing) throw new Error("blocked");
            map.set(k, String(v));
        },
        removeItem: (k: string) => void map.delete(k),
        clear: () => map.clear(),
        key: (i: number) => [...map.keys()][i] ?? null,
        get length() {
            return map.size;
        },
    };
    (globalThis as { window?: unknown }).window = { localStorage: store };
    return map;
}

// The width ladder is ordered and carries the documented default.
{
    for (let i = 1; i < WIDTH_STEPS.length; i += 1) {
        assert.ok(WIDTH_STEPS[i] > WIDTH_STEPS[i - 1], "widths must increase");
    }
    assert.equal(WIDTH_STEPS[defaultWidthIndex()], DEFAULT_WIDTH_CH);
    // 84ch is what the page shipped with, so the default must not move it.
    assert.equal(DEFAULT_WIDTH_CH, 84);
    // The widest rung is past a typical wide viewport: "no margins to speak of".
    assert.ok(WIDTH_STEPS[WIDTH_STEPS.length - 1] >= 160);
}

// clampWidthIndex: clamps rather than wraps, rounds, survives garbage.
{
    assert.equal(clampWidthIndex(-5), 0);
    assert.equal(clampWidthIndex(999), WIDTH_STEPS.length - 1);
    assert.equal(clampWidthIndex(1.6), 2);
    assert.equal(clampWidthIndex(Number.NaN), 0);
    assert.equal(widthChAt(999), WIDTH_STEPS[WIDTH_STEPS.length - 1]);
}

// The page default pairs the page font rung with the default width.
{
    const prefs = defaultReaderPrefs();
    assert.equal(FONT_STEPS[prefs.font], 16);
    assert.equal(WIDTH_STEPS[prefs.width], DEFAULT_WIDTH_CH);
    assert.equal(prefs.font, defaultFontIndex("page"));
}

// normalizeReaderPrefs: each axis falls back on its own, so one bad field does
// not discard a preference the reader did set.
{
    const fallback = defaultReaderPrefs();
    assert.deepEqual(normalizeReaderPrefs(null), fallback);
    assert.deepEqual(normalizeReaderPrefs("nonsense"), fallback);
    assert.deepEqual(normalizeReaderPrefs({}), fallback);
    assert.deepEqual(normalizeReaderPrefs({ font: 0, width: 0 }), { font: 0, width: 0 });
    // Out-of-range values clamp instead of resetting to the default.
    assert.deepEqual(normalizeReaderPrefs({ font: 99, width: 99 }), {
        font: FONT_STEPS.length - 1,
        width: WIDTH_STEPS.length - 1,
    });
    // One corrupt field keeps the other.
    assert.deepEqual(normalizeReaderPrefs({ font: 1, width: "wide" }), {
        font: 1,
        width: fallback.width,
    });
    assert.deepEqual(normalizeReaderPrefs({ font: Number.NaN, width: 1 }), {
        font: fallback.font,
        width: 1,
    });
}

// Round trip: what is written comes back.
{
    installStorage();
    assert.deepEqual(readReaderPrefs(), defaultReaderPrefs(), "empty store reads default");
    writeReaderPrefs({ font: 1, width: 4 });
    assert.deepEqual(readReaderPrefs(), { font: 1, width: 4 });
}

// A stored value is normalized on the way in and on the way out, so a record
// left by another version can never render an absurd column.
{
    const map = installStorage();
    map.set(READER_PREFS_STORAGE_KEY, JSON.stringify({ font: -10, width: 500 }));
    assert.deepEqual(readReaderPrefs(), { font: 0, width: WIDTH_STEPS.length - 1 });

    map.set(READER_PREFS_STORAGE_KEY, "{not json");
    assert.deepEqual(readReaderPrefs(), defaultReaderPrefs(), "malformed JSON reads default");

    writeReaderPrefs({ font: 99, width: -3 });
    assert.deepEqual(JSON.parse(map.get(READER_PREFS_STORAGE_KEY) as string), {
        font: FONT_STEPS.length - 1,
        width: 0,
    });
}

// Storage being unavailable degrades to the defaults; neither call throws.
{
    installStorage({ throwing: true });
    assert.deepEqual(readReaderPrefs(), defaultReaderPrefs());
    assert.doesNotThrow(() => writeReaderPrefs({ font: 2, width: 2 }));
}

// No `window` at all (the reading page's module graph is imported in odd
// contexts) must not throw either.
{
    delete (globalThis as { window?: unknown }).window;
    assert.deepEqual(readReaderPrefs(), defaultReaderPrefs());
    assert.doesNotThrow(() => writeReaderPrefs(defaultReaderPrefs()));
}

console.log("md-reader: ok");
