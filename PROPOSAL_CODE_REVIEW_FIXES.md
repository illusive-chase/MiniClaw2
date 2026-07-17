# Proposal: Fixes for the Native Code-Review Feature Reviews

Two independent reviews of the native code-review changes (the 15-finding
pipeline review and the 3-finding P1/P2 review) were cross-checked against the
code at `7f5e648`. **Every finding is real.** The second review's three
findings are a subset of the pipeline's (P1 = auto-promotion, P2a = snapshot
integrity + the additional unborn-HEAD case, P2b = rerun), so this doc merges
them into one fix plan.

## Verification verdicts

| # | Location | Verdict | Notes |
|---|----------|---------|-------|
| 1 | `git_state.py:163-164` | **Confirmed** | `_git` (`git_state.py:525-541`) swallows every exception — including the `text=True` strict-UTF-8 decode error and the 10 s timeout — into `returncode=1, stdout=""`; line 164 then silently drops the whole tracked diff. The second review adds a case the first missed: an **unborn HEAD** (fresh repo, staged files, no commits) makes `git diff HEAD` fail the same way. |
| 2 | `runner.py:417-424, 467` | **Confirmed** | `_run_code_review` publishes the report whenever `report is not None` (no `final_state` guard), then the non-DONE branch calls `_write_stub_preview` (`runner.py:1027-1028`) → `clear_published_artifacts` → `publish_artifacts(…, [], …)` which rmtree's the stored copy and empties `node.artifacts` (`artifacts.py:149-156`). A completed report followed by a failed/errored turn end is destroyed. |
| 3 | `registry.py:1554-1559` | **Confirmed** | The relocated check runs on every `update_virtual` call. `create_blank_planspace` seeds `prompt_draft=""` regular virtuals (`registry.py:1078-1081`); "Mark obsolete"/"Restore" sends only `{obsolete_reason}` (`AgentPanel.tsx:530-541`). Old code (`7f5e648^`) validated only when `prompt_draft` was explicitly passed, so this is a regression. |
| 4 | `registry.py:1156` | **Confirmed** (= second review P1) | `_next_promotion_candidate` (used by `_auto_promote_eligible_virtuals`, `registry.py:1110`) unconditionally requires a non-empty draft; `promote_virtual:1181-1184` and `virtualReady` (`AgentPanel.tsx:1666`) both carry the code_review exemption. Empty-prompt code_review virtuals stall AUTO lanes. |
| 5 | `registry.py:1536-1539, 1555` | **Confirmed** (severity note) | `PATCH {"category": "regular"}` on an empty-prompt code_review virtual: `update["subtype"]` is nulled but the local `next_subtype` stays `CODE_REVIEW`, skipping the check → persists as `category=regular, prompt_draft=""`. Promote then returns `None` → the generic 409 (`app.py:967-972`) that never mentions the prompt. "Permanently stuck" is slightly overstated — a later prompt edit unsticks it — but the deferred, opaque failure is real. |
| 6 | `claude.py:158-168, 239-243` | **Confirmed** | `_unknown_code_review_command` substring-matches the entire final report at done-time. The literal lives in this repo (`claude.py:241`, `backend/tests/test_claude_provider.py:49` — the review said `test_code_review.py`, minor inaccuracy), so a self-review quoting it discards the completed report. |
| 7 | `codex.py:169-176, 595-600` | **Confirmed** | `_read_stdout` keeps only `error["message"]`, discarding the JSON-RPC `code`; the classifier then sniffs `"method" in str(exc).lower() or "review/start" in str(exc)`, rewriting e.g. a `-32602 invalid params for method review/start` bug as "upgrade codex-cli". |
| 8 | `registry.py:1550-1553` | **Confirmed** | `domain.py:323-325` auto-populates `ReviewTarget()` on every code_review node, so `next_review_target` is never `None` unless the caller explicitly sends `review_target: null`; switching subtype away from code_review then 400s on a field the caller never sent. |
| 9 | `registry.py:1705-1707` | **Confirmed** (= second review P2b) | code_review nodes are spawned with `prompt=""` and no draft (`registry.py:953-961`), so `rerun_node` raises for every failed/cancelled review while the UI offers Rerun. Also noted: the rerun path doesn't pass `review_target` to `create_virtual` (today harmless — `create_virtual:1367-1368` defaults it — but should be carried explicitly). |
| 10 | `SidePanel.tsx:447`, `App.tsx:1698-1719`, `app.py:646-657` | **Confirmed** | `gitAction` clears in the `finally` as soon as the POST resolves; there is no nodes-derived `reviewInFlight` (contrast `pullInFlight`, `App.tsx:1692-1697`) and no backend dedupe/quiescence guard (contrast `/git/pull`, `app.py:665-666`). |
| 11 | `codex.py:161-168` | **Confirmed** | `spec.focus` is silently dropped (params hardcoded); `PROPOSAL_CODE_REVIEW.md:354` acknowledges the missing channel and the promised surfacing was never shipped; the executed preview's motivation echoes `brief.check_what` (`runner.py:451-455`) as if applied. |
| 12 | `AgentPanel.tsx:855-860` | **Confirmed** | Save is gated by `!draft.promptDraft.trim()` with no code_review exemption; `virtualReady` (line 1666) and every backend path have it. Empty-prompt code_review virtuals are uneditable from the UI. |
| 13 | `runner.py:399`, `registry.py:950` | **Confirmed** | Both `git_status` calls run synchronously on the event loop; `git_status` reads every untracked file in full (`_untracked_file_stat`, `git_state.py:232-252`). Siblings wrap identical work in `asyncio.to_thread` (`registry.py:894, 972-977`; `runner.py:402-404, 418-420`). |
| 14 | `claude.py:159`, `transcript.py:206-209` | **Plausible** (as the review itself labels it) | `_last_assistant_text` is overwritten by every assistant record with a text block — no sidechain or finality filter. Current Claude Code behavior happens to end turns with the report, but nothing guarantees it; an interrupted turn publishes whatever came last. |
| 15 | `App.tsx:1140-1173`, `runner.py:395-408`, `AgentPanel.tsx:287` | **Confirmed** | RUNNING is emitted before `reviewed-diff.patch` is written, and none of the effect deps change mid-run, so the fetch 404s until the terminal transition; a clean-tree review (reachable via direct API — `spawn_code_review` checks only `is_repo`) never writes the file, so the placeholder is permanent. The shipped UI disables the button at `dirtyCount === 0`, so the permanent case is API-only. |

The three findings the pipeline cut (pull-drain doc mismatch, patch `rstrip`
normalization, abandoned async-generator cleanup) stay cut — no action.

---

## Fix plan

Ordered by priority. Themes 1–3 are functional regressions / data loss;
4–6 are misleading-failure bugs; 7–9 are hardening and UX.

### 1. Snapshot integrity — never publish a truncated audit patch (finding 1, P2a) — **P1**

`git_review_snapshot` (`git_state.py:159-185`):

- **Fail loudly instead of degrading.** If the tracked diff fails, do not hash
  a partial patch. Either raise (and let `_run_code_review` turn it into a node
  ERROR with the git stderr in `node.error`) or add an `error: str | None`
  field to `GitReviewSnapshot` that the runner checks before writing
  `reviewed-diff.patch` and launching the provider. Raising is simpler and
  matches how the runner already handles unexpected exceptions.
- **Handle the unborn HEAD.** When `status.head is None` (porcelain reports
  `branch.oid (initial)`), diff against Git's empty tree instead of `HEAD`:
  `git diff $(git hash-object -t tree /dev/null) --binary --no-ext-diff`
  (or the well-known constant `4b825dc642cb6eb9a060e54bf8d69288fbee4904`).
- **Make the capture decode-tolerant.** Run this diff (and the untracked
  `--no-index` diffs at lines 173-178) in bytes mode and decode with
  `errors="replace"` (a `_git_bytes` variant of `_git`), so one Latin-1 byte in
  a tracked file cannot zero out the snapshot. The fingerprint is computed on
  the decoded text either way, so both mid-run staleness snapshots stay
  comparable.
- **Raise the timeout** for this call (e.g. 60 s) — a 10 s cap on a full-tree
  binary diff is easy to blow on large repos, and a timeout must also surface
  as an error, not an empty patch.

### 2. Stop destroying published review reports (finding 2) — **P1**

`_run_code_review` (`runner.py:437-469`): the non-DONE branch must not clear
artifacts that were just published.

- Change the else branch to check whether the report was published:

  ```python
  elif artifacts:
      # keep the published report; record the failure alongside it
      self._persist_executed_preview(
          final_state,
          motivation=...,
          summary=f"review completed but the turn ended {final_state.value}: {error_msg}",
          next_implications=...,
          artifacts=artifacts,
      )
  else:
      self._write_stub_preview(final_state, reason=error_msg or "code review cancelled")
  ```

- Alternatively (or additionally) give `_write_stub_preview` a
  `keep_artifacts: bool = False` parameter. The minimal invariant: once
  `publish_artifacts` has stored a code-review report, no later step in the
  same run may call `clear_published_artifacts`.

### 3. Finish the "empty-prompt code_review" exemption everywhere (findings 3, 4, 5, 9, 12; P1, P2b) — **P1**

The root cause of five findings is one predicate applied in only two of six
places. Centralize it:

```python
def _virtual_requires_prompt(subtype: ReviewSubtype | None) -> bool:
    return subtype is not ReviewSubtype.CODE_REVIEW
```

- **`_next_promotion_candidate` (`registry.py:1156`)** — replace the
  unconditional draft check with
  `if _virtual_requires_prompt(n.subtype) and not (n.prompt_draft or "").strip(): continue`.
  This unstalls AUTO lanes (finding 4 / P1). Add a regression test: AUTO lane +
  planner-proposed empty-prompt code_review virtual auto-promotes.
- **`rerun_node` (`registry.py:1705-1707`)** — skip the prompt requirement for
  code_review originals and pass `review_target=original.review_target` through
  to `create_virtual` (finding 9 / P2b). Keep the error for all other
  promptless nodes.
- **`update_virtual` (`registry.py:1536-1559`)** — two changes:
  1. When `next_category is not Category.REVIEW`, also reset the local
     `next_subtype = None` (and `next_brief`/`next_review_target`) so the
     deferred checks see the *resulting* node, not the stale one. This closes
     the category-switch hole (finding 5): converting a code_review virtual to
     regular with an empty draft now 400s upfront again.
  2. Restore the old scoping so metadata-only edits pass (finding 3): enforce
     the non-empty-prompt rule only when the request actually touches the
     prompt-relevant shape — i.e. when `prompt_draft`, `category`, or `subtype`
     was explicitly passed. A `{obsolete_reason}`-only PATCH on a blank virtual
     must succeed, exactly as before this change.
- **`AgentPanel.tsx:858`** — mirror `virtualReady`: disable Save on
  `!draft.promptDraft.trim()` only when the draft's subtype isn't
  `code_review` (finding 12).

### 4. `review_target` API ergonomics on subtype switch (finding 8) — **P2**

`update_virtual` (`registry.py:1550-1553`): the server auto-populates
`review_target` on every code_review virtual (`domain.py:323-325`), so it must
also auto-discard its own default. When `review_target is _UNSET` and the
subtype is switching away from `CODE_REVIEW`, treat `next_review_target` as
`None` instead of raising. Keep the 400 only when the caller *explicitly*
sends a non-null `review_target` for a non-code_review virtual.

### 5. Provider error classification (findings 6, 7) — **P2**

- **`claude.py`** — stop substring-matching the whole report. The real
  unknown-command reply is a short, standalone message; anchor the check, e.g.:

  ```python
  def _unknown_code_review_command(text: str) -> bool:
      stripped = text.strip().lower()
      return len(stripped) < 200 and stripped.startswith("unknown slash command")
      # (keep the "/code-review" variant with the same anchoring)
  ```

  A multi-paragraph review that merely *quotes* the phrase no longer matches.
  Update `test_claude_provider.py` with a counter-case: a long report
  containing the literal must be published, not discarded.
- **`codex.py`** — preserve the structured error. Have `_read_stdout`
  (`codex.py:595-600`) raise a typed exception carrying the code:

  ```python
  class CodexRpcError(RuntimeError):
      def __init__(self, code: int | None, message: str): ...
  ```

  Then classify the version gap in `run_review` precisely:
  `except CodexRpcError as exc: if exc.code == -32601: <upgrade message>` —
  everything else propagates verbatim into `node.error`/logs. Delete the
  `"method" in str(exc).lower()` sniff.

### 6. Event-loop blocking at review launch (finding 13) — **P2**

- **`runner.py:399`** — drop the separate `git_status` call entirely. Take the
  snapshot first (already via `to_thread`) and branch on
  `snapshot.dirty_paths`: empty → "working tree clean — nothing to review".
  One subprocess pass instead of two, and nothing runs on the loop.
- **`registry.spawn_code_review` (`registry.py:950`)** — the handler only needs
  `is_repo`; replace the full `git_status` (which reads every untracked file)
  with a cheap `git rev-parse --git-dir` executed via `asyncio.to_thread`
  (making `spawn_code_review` async, or hoisting the check into the async
  `git_review` handler in `app.py`).

### 7. Review button dedupe (finding 10) — **P2**

Two layers, mirroring pull:

- **Frontend** — derive `reviewInFlight` in `App.tsx` the same way as
  `pullInFlight` (any node with `subtype === "code_review"` in
  `queued`/`running`), and feed it into the Review button's disabled state so
  it stays down for the node's whole life, not just the POST.
- **Backend** — in `spawn_code_review`, if a code_review node is already
  queued/running for the project, return that node (idempotent) or raise a
  `ValueError` → 409. Returning the existing node is friendlier: a double-click
  simply focuses the in-flight review.

### 8. Codex focus limitation must be surfaced (finding 11) — **P3**

The API genuinely has no focus channel for `uncommittedChanges`
(`PROPOSAL_CODE_REVIEW.md:354`), so the fix is honesty, not plumbing:

- When `spec.focus` is set and the provider is Codex, prepend a caveat to the
  published report / executed preview: *"Note: the focus text was not applied —
  Codex native review does not support focus on uncommitted changes."* The
  runner can do this generically if `run_review` yields a
  `focus_applied: bool` (or the provider strips `spec.focus` and emits a
  warning event).
- Annotate the "Focus (optional)" field in the UI when the selected preset is
  a Codex preset.
- Ship the docs note the proposal promised.

### 9. Report-capture and reviewed-diff panel robustness (findings 14, 15) — **P3**

- **`transcript.py` (finding 14)** — two cheap hardenings: skip records with
  `isSidechain: true` when updating `_last_assistant_text`, and track
  assistant texts per turn so the done-time report picks the longest text of
  the turn rather than literally the last (a trailing "Review complete."
  can no longer displace a multi-KB report). Keep the PLAUSIBLE framing: this
  is defensive, current CC versions happen to behave.
- **Finding 15**, three small pieces:
  1. `runner.py` — write `reviewed-diff.patch` *before* emitting the RUNNING
     transition (reorder lines 395-408), so the state-triggered fetch can't
     lose the race.
  2. Clean-tree runs — write a one-line placeholder patch (or have
     `/nodes/{nid}/reviewed-diff` answer with explanatory text when the node's
     summary is the clean-tree message) so the panel can say "nothing to
     review" instead of "Snapshot not available yet." forever.
  3. `App.tsx` — as a belt-and-suspenders, retry the reviewed-diff fetch once
     after a short delay when it 404s while `state === "running"`.

---

## Suggested sequencing

1. **Theme 3** first — it's one predicate, fixes five findings including both
   remaining P1-class regressions (AUTO-lane stall, rerun), and its
   `update_virtual` piece unbreaks "Mark obsolete".
2. **Themes 1–2** next — audit-integrity and data-loss fixes, independent of
   everything else.
3. **Themes 4–7** in any order; each is small and isolated.
4. **Themes 8–9** last; UX/hardening.

Test coverage to add along the way: unborn-repo + non-UTF-8 snapshot tests
(`git_state`), a report-published-then-turn-failed runner test, AUTO-lane
auto-promotion of a promptless code_review virtual, rerun of a failed
`/git/review` node, `update_virtual` metadata-only edit on a blank virtual,
category-switch-away-from-review with empty draft (must 400), subtype switch
without explicit `review_target` (must succeed), and the anchored
unknown-slash-command matcher against a long report quoting the phrase.
