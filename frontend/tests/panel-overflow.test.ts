import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

/* The node-details side panel is a fixed 380px column (App.tsx `w-[380px]`)
 * that renders agent-authored free text: node summaries, preview fields,
 * dependency lines, planspace ids, file paths. Those strings routinely
 * contain a token with no break opportunity — `planspaces.miniclaw2-dev-tc.
 * templating2`, or a path like `src/canvas/edges/DependencyConnectionLine.tsx`
 * — which is wider than the panel.
 *
 * A CSS grid track written `1fr` is shorthand for `minmax(auto,1fr)`, and that
 * `auto` minimum floors the track at the content's *min-content* width. So one
 * long token stretches the track past the panel; the card's `overflow-hidden`
 * then silently clips whatever spills, and `overflow-y-auto` on the panel body
 * computes `overflow-x` to `auto`, so the whole panel scrolls sideways.
 *
 * The subtlety worth locking down: neither `break-words` nor `truncate`
 * prevents this. `overflow-wrap: break-word` wraps glyphs visually but leaves
 * min-content unchanged, and a `truncate` item's own `overflow: hidden` does
 * not feed back into the track's `auto` minimum. Only writing the track as
 * `minmax(0,1fr)` removes the floor.
 *
 * These are text assertions over source rather than layout assertions because
 * the repo has no DOM/browser harness. They catch the reintroduction of the
 * pattern, which is the realistic regression. */

function read(relative: string): string {
  return readFileSync(
    fileURLToPath(new URL(`../src/${relative}`, import.meta.url)),
    "utf8",
  );
}

const PANEL_FILES = [
  "panel/AgentPanel.tsx",
  "panel/SidePanel.tsx",
  "panel/InspectDrawer.tsx",
  "panel/ProjectPanel.tsx",
  "panel/ArtifactPanel.tsx",
];

/* Tracks holding a short closed vocabulary cannot overflow, so they are
 * allowed to stay bare rather than be churned. Each entry is the exact
 * className fragment plus why it is safe. */
const ALLOWED_BARE_TRACKS = new Map<string, string>([
  [
    "panel/SidePanel.tsx:grid grid-cols-[120px_1fr] gap-3",
    "value is the planspace mode enum (auto|manual) and an 8-char node id",
  ],
]);

/** Every `grid-cols-[…]` arbitrary value found in a file. */
function gridTracks(source: string): string[] {
  return [...source.matchAll(/grid grid-cols-\[[^\]]*\][^"'`]*/g)].map((m) => m[0]);
}

/* A bare `1fr` — i.e. `1fr` not preceded by `minmax(0,` — in a panel grid. */
function bareOneFr(track: string): boolean {
  const columns = track.slice(track.indexOf("[") + 1, track.indexOf("]"));
  return columns.split("_").some((column) => column === "1fr");
}

for (const file of PANEL_FILES) {
  const source = read(file);
  for (const track of gridTracks(source)) {
    if (!bareOneFr(track)) continue;
    const key = `${file}:${track.trim()}`;
    const waiver = [...ALLOWED_BARE_TRACKS.entries()].find(([allowed]) =>
      key.startsWith(allowed),
    );
    assert.ok(
      waiver,
      `${file}: grid track "${track.trim()}" uses a bare \`1fr\`. In the ` +
        "380px side panel that floors the column at the content's min-content " +
        "width, so a long id or path widens the card and gets clipped by " +
        "`overflow-hidden`. Write `minmax(0,1fr)`, or add a waiver to " +
        "ALLOWED_BARE_TRACKS if the value is a short closed vocabulary.",
    );
  }
}

/* The two cards the user actually reported: preview fields (motivation,
 * summary, next_implications) and the Basic-information key/value grid. Both
 * sit inside `overflow-hidden` cards, so a regression here clips silently. */
{
  const source = read("panel/AgentPanel.tsx");

  assert.match(
    source,
    /<dl className="grid grid-cols-\[minmax\(0,1fr\)\] gap-2">/,
    "PreviewCard's field list must pin its single column to `minmax(0,1fr)`: " +
      "an implicit `auto` column floors at the widest unbreakable token in " +
      "the motivation / summary / next_implications text.",
  );

  assert.match(
    source,
    /grid grid-cols-\[120px_minmax\(0,1fr\)\] gap-x-3 gap-y-1\.5/,
    "KVGrid must use `minmax(0,1fr)`: it renders the planspace id and node " +
      "id, single tokens wider than the panel.",
  );
}

/* Free-text spans need an explicit wrapping rule. `break-words` does not fix
 * a track floor, but once the track is capped it is what actually breaks the
 * token instead of letting it spill. */
{
  const source = read("panel/AgentPanel.tsx");

  const depRow = source.match(
    /<span className="ml-2([^"]*)text-ink">\s*\{oneLine\(dep\.summary/,
  );
  if (!depRow) {
    throw new Error("expected the executed-node dependency summary span");
  }
  assert.match(
    depRow[1],
    /break-words/,
    "the dependency summary is agent-authored and can cite a path with no " +
      "break opportunity; without `break-words` it overflows its card.",
  );

  const candidateRow = source.match(
    /<span className="min-w-0([^"]*)">\s*<span className="font-mono text-ink-muted">\s*\{candidate\.id/,
  );
  if (!candidateRow) {
    throw new Error("expected the dependency-candidate label span");
  }
  assert.match(
    candidateRow[1],
    /break-words/,
    "`min-w-0` lets the flex item shrink below min-content, but only a " +
      "wrapping rule breaks the token inside that reduced width.",
  );
}

/* Agent-authored Markdown is rendered through `.md-prose` inside the same
 * 380px panel (AgentPanel transcript, gate review, planspace files). Two of
 * its element types have no intrinsic width limit. */
{
  const css = read("index.css");

  const inlineCode = css.match(/\.md-prose code \{([^}]*)\}/);
  if (!inlineCode) throw new Error("expected a `.md-prose code` rule");
  assert.match(
    inlineCode[1],
    /overflow-wrap:\s*anywhere/,
    "inline code is the usual carrier of an unbreakable token (a path, a " +
      "planspace id). `anywhere` rather than `break-word` because only it " +
      "also lowers min-content, which is what stops the span widening a " +
      "flex/grid ancestor that would clip it.",
  );

  const preCode = css.match(/\.md-prose pre code \{([^}]*)\}/);
  if (!preCode) throw new Error("expected a `.md-prose pre code` rule");
  assert.match(
    preCode[1],
    /overflow-wrap:\s*normal/,
    "a fenced block must NOT inherit `anywhere`: it already scrolls via " +
      "`overflow-x: auto`, and breaking tokens mid-identifier corrupts how " +
      "the code reads.",
  );

  /* Verified against real react-markdown + remark-gfm output: a GFM table is
   * emitted as a direct child of the `.md-prose` container, so the child
   * combinator matches. */
  const table = css.match(/\.md-prose > table \{([^}]*)\}/);
  if (!table) throw new Error("expected a `.md-prose > table` rule");
  assert.match(
    table[1],
    /display:\s*block/,
    "a bare `table` ignores `overflow`; `display: block` is what makes the " +
      "box scrollable so a wide table does not push the panel sideways.",
  );
  assert.match(table[1], /overflow-x:\s*auto/, "the table box must scroll");
}

console.log("panel-overflow: ok");
