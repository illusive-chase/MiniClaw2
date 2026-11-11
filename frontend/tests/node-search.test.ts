import assert from "node:assert/strict";
import {
  highlightSegments,
  searchFieldLabel,
  splitSearchTerms,
  termRanges,
} from "../src/nodeSearch";

// Terms split on whitespace and CJK punctuation, lower-cased and de-duplicated,
// exactly as the backend's `split_terms` does.
assert.deepEqual(splitSearchTerms("  代码阅读，Preview。缓存  preview ;x "), [
  "代码阅读",
  "preview",
  "缓存",
  "x",
]);
assert.deepEqual(splitSearchTerms(" ，。 "), []);
assert.equal(splitSearchTerms("a b c d e f g h i j").length, 8);

// Ranges are case-insensitive and overlapping hits merge.
assert.deepEqual(termRanges("Feishu feishu", ["feishu"]), [
  [0, 6],
  [7, 13],
]);
assert.deepEqual(termRanges("abcd", ["ab", "bc"]), [[0, 3]]);
assert.deepEqual(termRanges("abc", []), []);

assert.deepEqual(highlightSegments("实现了 Feishu 卡片信箱", ["feishu", "信箱"]), [
  { text: "实现了 ", hit: false },
  { text: "Feishu", hit: true },
  { text: " 卡片", hit: false },
  { text: "信箱", hit: true },
]);
assert.deepEqual(highlightSegments("no match", ["zzz"]), [
  { text: "no match", hit: false },
]);
assert.deepEqual(highlightSegments("", ["x"]), []);

assert.equal(searchFieldLabel("text"), "回复");
assert.equal(searchFieldLabel("summary"), "结果摘要");

console.log("node-search tests passed");
