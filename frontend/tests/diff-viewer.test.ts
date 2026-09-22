import assert from "node:assert/strict";

import { alignLines } from "../src/components/DiffViewer";

{
  const rows = alignLines("value", "value\n");
  assert.equal(rows.length, 2);
  assert.equal(rows[0].changed, false);
  assert.equal(rows[1].changed, true);
  assert.equal(rows[1].left?.eofMarker, true);
  assert.equal(rows[1].right, undefined);
}

{
  const rows = alignLines("value\n", "value");
  assert.equal(rows.at(-1)?.right?.eofMarker, true);
  assert.equal(rows.at(-1)?.left, undefined);
}

{
  const rows = alignLines("value\n", "value\n");
  assert.equal(rows.length, 1);
  assert.equal(rows[0].changed, false);
}

{
  const rows = alignLines(null, "value");
  assert.equal(rows.at(-1)?.right?.eofMarker, true);
  assert.equal(rows.at(-1)?.left, undefined);
}
