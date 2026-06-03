# MiniClaw2 Memory Protocol Proposal

Status: discussion draft.

This proposal defines a filesystem memory protocol for MiniClaw2 nodes.
The goal is not to make agents read more text by default. The goal is to
make cross-node memory explicit, scoped, auditable, and safe under forks,
parallel branches, and human review.

## 1. Goals

MiniClaw2 already has the right runtime spine:

- `Project`, `Node`, and `HumanGate` persist under `$MINICLAW_HOME`.
- Per-node `node.json`, `events.jsonl`, and `gates.jsonl` are the
  authoritative runtime record.
- Project timelines are serialized; concurrency comes from forks or
  separate projects, not multiple writers in one worktree.
- Provider conversation continuation is explicit through resume edges,
  not implied by timeline adjacency.
- A project-root `CONTEXT.md` is loaded at node launch and snapshotted
  onto the node.

The memory protocol should extend that spine without replacing it.

The protocol must support:

- Stable project-level principles.
- Reusable global skills and project-local skill overrides.
- Frequently updated project state and plans.
- Explicit manual vs automatic update policy.
- Per-node context snapshots for audit and replay.
- Branch/fork-local state without cross-branch overwrite hazards.
- A distinction between "the executor finished" and "the result was
  accepted".

## 2. Non-Goals

This is not a full ARIS-style skill ecosystem.

This is not a replacement for `$MINICLAW_HOME/projects/<pid>/...`.
Runtime state stays in the existing JSON/JSONL store. Files in the
project root are collaboration memory, not the source of truth for node
state transitions.

This is not a plan to run several agent nodes concurrently in the same
project worktree. MiniClaw2 should keep the current single-runner
project invariant. Parallelism should come from project forks,
worktrees, or isolated temporary workspaces.

This is not a plan to inject every memory file into every node. Context
must be selected, bounded, and snapshotted.

## 3. Memory Classes

MiniClaw2 should treat memory as several distinct classes.

| Class | Examples | Update Rate | Default Policy | Injection |
|---|---|---:|---|---|
| Project principles | architecture rules, coding conventions, durable "do not" rules | low | manual/proposed | system context |
| Skills | reusable tool/workflow knowledge, script conventions | low | manual/proposed | selected per node |
| Project state | active plan, recent decisions, branch status, open blockers | high | automatic or semi-automatic | short turn context |
| Runtime facts | node events, gates, provider ids, artifacts, usage | very high | system-owned | not directly injected |
| Memory deltas | node-proposed updates to state/principles/skills | medium | append-only inbox | reviewed/applied later |

The important design point: a node may freely produce observations, but
only some observations are automatically promoted into shared memory.

## 4. Global vs Project-Local Scope

MiniClaw2 needs both global and local memory.

Global memory is user-wide and reusable across projects:

```text
$MINICLAW_HOME/
  memory/
    global/
      CONTEXT.md
      manifest.yaml
    skills/
      <skill-id>/
        manifest.yaml
        CONTEXT.md
        assets/
```

Project-local memory lives in the project root so the user can read it
directly without opening MiniClaw2 internals:

```text
<project_root>/
  CONTEXT.md                 # stable project principles; manual/proposed
  STATUS.md                  # short current status; automatic
  PLAN.md                    # current plan; semi-automatic
  SKILLS.md                  # optional human-readable index of active skills
  miniclaw/
    manifest.yaml            # project memory policy
    skills/
      <skill-id>/
        manifest.yaml
        CONTEXT.md
        assets/
    state/
      events.jsonl           # append-only project memory events
      checkpoints/
        <bundle-id>.json
    inbox/
      <node-id>.memory-delta.json
      <node-id>.notes.md
    snapshots/
      <bundle-id>.json
```

The visible root files are for humans and agents. The `miniclaw/`
directory is still visible, but it holds structured files that should
not clutter the root.

Project-local skill definitions override or extend global skills with
the same `skill-id`. Suggested resolution order:

1. Project-local `miniclaw/skills/<skill-id>/`.
2. User-global `$MINICLAW_HOME/memory/skills/<skill-id>/`.
3. Bundled MiniClaw2 skills, if any are later added.

Global skills should be stable and reusable. Project-local skills should
capture project-specific wrappers, scripts, paths, and local workflow
constraints.

## 5. Root File Protocol

### `CONTEXT.md`

Stable project principles and conventions.

Default write policy: `proposed`.

Agents should not directly rewrite this file unless the user explicitly
asks for it. Instead, they should write a memory delta proposing the
change. A human gate, review node, or future memory-maintainer op can
apply the proposal.

Suggested contents:

- Project purpose and architecture.
- Important directory and test conventions.
- Durable coding rules.
- Durable safety rules.
- Short references to longer docs.

Avoid:

- Current task progress.
- Long logs.
- Provider-specific runtime state.
- Large pasted plans.
- Secrets or machine-local tokens.

### `STATUS.md`

Short current project state.

Default write policy: `auto`.

MiniClaw2 may update this after node completion, gate resolution,
verifier results, branch changes, or memory-delta application. It should
be concise and generated from structured facts. It should include enough
information for a fresh node to orient quickly, but not enough to become
a transcript replacement.

Suggested sections:

```markdown
# Project Status

Generated: <timestamp>
Project: <project id or name>
Branch/worktree: <git branch or commit>

## Current Objective

## Recent Accepted Changes

## In Progress / Unaccepted Work

## Open Questions and Blockers

## Next Useful Actions
```

`STATUS.md` may mention unaccepted work, but it must label it as such.
It must not silently convert a node's self-report into accepted project
truth.

### `PLAN.md`

Current working plan.

Default write policy: `proposed`.

This file is semi-automatic. A planner node may propose an update, but
automatic worker nodes should not rewrite it silently. This avoids the
common failure mode where every worker reshapes the plan to match what
it just did.

Suggested sections:

```markdown
# Project Plan

## Active Goal

## Milestones

## Decisions

## Branches / Parallel Work

## Deferred Work
```

### `SKILLS.md`

Optional human-readable index of active global and local skills.

Default write policy: `auto`.

This should not duplicate skill contents. It should list skill ids,
scope, one-line purpose, and where the real `CONTEXT.md` lives.

## 6. Manifest Protocol

Every structured memory directory should have a `manifest.yaml`.

For a project:

```yaml
version: 1
type: project-memory
write_policy:
  CONTEXT.md: proposed
  STATUS.md: auto
  PLAN.md: proposed
  SKILLS.md: auto
git_policy:
  CONTEXT.md: user_decides
  STATUS.md: user_decides
  PLAN.md: user_decides
  miniclaw/state/events.jsonl: local
  miniclaw/inbox: local
context_budget:
  system_max_chars: 8000
  turn_state_max_chars: 4000
active_skills: []
```

For a skill:

```yaml
version: 1
name: <skill-id>
type: skill
scope: global | project
description: ""
requires: []
tags: []
write_policy: proposed
injection: turn
max_chars: 6000
```

Machine-readable data belongs in `manifest.yaml`. Prompt-facing
instructions belong in `CONTEXT.md`.

## 7. Context Bundle Snapshot

At node launch, MiniClaw2 should compose a context bundle from selected
sources and snapshot it onto the node.

This should replace the current single-string mental model while
remaining backward-compatible with `system_context_snapshot`.

Proposed snapshot shape:

```json
{
  "bundle_id": "abc123",
  "created_at": 1234567890,
  "sources": [
    {
      "scope": "project",
      "kind": "principles",
      "path": "CONTEXT.md",
      "sha256": "...",
      "chars": 1200,
      "injection": "system"
    },
    {
      "scope": "project",
      "kind": "state",
      "path": "STATUS.md",
      "sha256": "...",
      "chars": 900,
      "injection": "turn"
    },
    {
      "scope": "global",
      "kind": "skill",
      "id": "python-testing",
      "path": "$MINICLAW_HOME/memory/skills/python-testing/CONTEXT.md",
      "sha256": "...",
      "chars": 1500,
      "injection": "turn"
    }
  ],
  "system_text": "...",
  "turn_text": "..."
}
```

Provider mapping:

- Claude: stable project principles can continue to use
  `system_prompt.append`.
- Codex: stable project principles are prepended on fresh threads as
  today.
- State and skill context should be included in launch/turn text so
  resumed provider threads can still receive current project state when
  appropriate.

Every injected source needs a hash and path. This makes later audits
answerable: "what exactly did the node see?"

## 8. Memory Delta Protocol

Nodes should not freely rewrite long-lived memory files. They should
write proposed memory deltas.

Path:

```text
miniclaw/inbox/<node-id>.memory-delta.json
```

Shape:

```json
{
  "version": 1,
  "node_id": "<node id>",
  "created_at": 1234567890,
  "updates": [
    {
      "target": "STATUS.md",
      "operation": "append_observation",
      "policy": "auto",
      "confidence": "observed",
      "evidence": {
        "artifact": ".miniclaw2/outputs/<node-id>/result.md",
        "event_seq": 42
      },
      "text": "Implemented X; tests Y passed; blocker Z remains."
    },
    {
      "target": "CONTEXT.md",
      "operation": "propose_patch",
      "policy": "proposed",
      "reason": "A durable project convention was discovered.",
      "patch": "..."
    }
  ]
}
```

Application rules:

- `auto` updates may be applied by MiniClaw2 after the node reaches a
  terminal state.
- `proposed` updates remain in inbox until a human gate, deterministic
  verifier, or approved maintainer node accepts them.
- Failed or cancelled nodes may still write observations, but their
  deltas must be labeled with the terminal state and should not be
  promoted to long-lived principles automatically.
- A rejected review gate should prevent the source node's proposed
  durable memory from being promoted.

## 9. Done vs Accepted

MiniClaw2 should separate execution completion from acceptance.

Current `NodeState.DONE` should keep its meaning: the runner finished
successfully.

Add a separate acceptance concept:

```python
AcceptanceState = (
    "not_applicable"
    | "unreviewed"
    | "accepted"
    | "rejected"
    | "blocked"
)
VerdictSource = (
    "none"
    | "human"
    | "deterministic"
    | "cross_provider"
    | "same_provider_advisory"
)
```

Candidate node fields:

```python
acceptance_state: AcceptanceState = "unreviewed"
verdict_source: VerdictSource = "none"
verdict_artifact_path: str | None = None
verdict_thread_id: str | None = None
accepted_at: float | None = None
rejected_at: float | None = None
```

Type-A gates are objective:

- verifier exit code
- artifact exists
- tests pass
- commit op succeeds
- N/N jobs complete

Type-A gates may be accepted by deterministic backend logic.

Type-B gates are quality or correctness judgments:

- "the implementation is good"
- "the design is correct"
- "the memory update should become a durable rule"
- "the branch should merge"

Type-B gates require a human verdict or a different model family. Same
provider self-review can be recorded as advisory, but should not mark
the node accepted.

This distinction should drive memory promotion. A completed node can
update `STATUS.md` as "done but unreviewed"; it should not update
`CONTEXT.md` as durable truth until accepted.

## 10. Branch and Parallel Agent Rules

Within one project worktree, keep the single-runner invariant.

Parallel work should use project forks or worktrees. Each fork has its
own root-visible memory files and branch-local `miniclaw/state/`.

When a fork is created:

- Copy or reference global skills.
- Copy project `CONTEXT.md`, `PLAN.md`, and current `STATUS.md`.
- Record the parent project id and parent commit as today.
- Mark the fork status as branch-local.

When a fork returns:

- Do not auto-merge memory files by text overwrite.
- Import fork memory deltas into the parent inbox.
- Let a merge gate decide which deltas are accepted into parent memory.
- Treat code merge acceptance and memory promotion as related but
  distinct verdicts.

Same-family fan-out may generate candidates, collect evidence, or draft
alternatives. It must not act as the final Type-B jury. A cross-provider
review, deterministic verifier, or human gate must own acceptance.

## 11. Suggested First Implementation Slice

The first slice should be intentionally small.

1. Add `Node` acceptance fields while keeping `NodeState` unchanged.
2. Add a memory loader that reads root `CONTEXT.md`, `STATUS.md`,
   `PLAN.md`, selected global skills, and selected project-local skills.
3. Add `context_bundle_snapshot` to `Node` or a sibling persisted bundle
   file under `nodes/<nid>/`.
4. Continue populating `system_context_snapshot` for backward
   compatibility from the stable project-principles portion.
5. Add a basic `STATUS.md` auto-writer that updates only after node
   terminal transitions and gate/verifier outcomes.
6. Add `miniclaw/inbox/<node-id>.memory-delta.json` support, but only
   auto-apply deltas targeting `STATUS.md`.
7. Update passive gate resolution so a gate can write acceptance fields
   on its source node.
8. Surface acceptance and context bundle sources in the node detail UI.

Defer:

- Full skill authoring UI.
- Cross-provider reviewer nodes.
- Fork merge UI.
- Automatic edits to `CONTEXT.md` or `PLAN.md`.
- Manifest-driven git ignore edits.
- Global skill marketplace.

## 12. Open Design Questions

The current draft assumes:

- Root-visible `CONTEXT.md`, `STATUS.md`, and `PLAN.md`.
- Root-visible `miniclaw/` for structured memory.
- User-global reusable skills under `$MINICLAW_HOME/memory/skills/`.
- `STATUS.md` automatic, `PLAN.md` semi-automatic, `CONTEXT.md` manual
  or proposed.
- `done` vs `accepted` should enter the domain model.

Questions still worth deciding before implementation:

1. Should MiniClaw2 ever auto-create `STATUS.md` in a repo without
   asking, or only after the user opts into memory protocol?
2. Should `STATUS.md` be committed by default, ignored by default, or
   left to user policy?
3. Should global skills be selectable per project in UI, or only through
   `miniclaw/manifest.yaml` in the first version?
4. Should `PLAN.md` be injected into ordinary worker nodes, or only into
   planner/review nodes?
5. Should deterministic verifier results accept the source node directly,
   or should they create a separate verifier op node with its own
   acceptance verdict?

