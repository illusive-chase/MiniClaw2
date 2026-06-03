# MiniClaw2 ContextSpace Proposal

Status: discussion draft, iteration 2.

This proposal defines a global filesystem context system for MiniClaw2.
The key shift from the previous draft is that project status, plans, and
skills should not be written into the project repository by default.
They should live in a separate, user-owned, git-maintained ContextSpace
and connect to MiniClaw2 projects through explicit bindings.

The project root still keeps `CONTEXT.md`, but its role is narrower:
it is codebase-facing guidance for agents reading and editing that
repository. Product/UX state such as active plans, branch status, loaded
skills, and parallel directions belongs to ContextSpace.

## 1. Core Model

MiniClaw2 should treat context as a graph:

```text
Project  <--binding edge-->  Plug
Node     <--snapshot edge--> ContextBundle
Plug     <--requires edge--> Plug
Project  <--fork edge------> Project
Node     <--resume edge----> Node
```

The important consequence: context is not hardcoded into a project
directory. A project can have several context plugs attached, and the
same plug can be reused across projects. Future UX can expose this as
"drag a plug onto a project" or "connect this planspace to that branch."

MiniClaw2 already models `Project`, `Node`, gates, node events, and
resume edges. ContextSpace adds a separate graph of reusable context
objects and project-context bindings.

## 2. Goals

The context system should optimize for:

- Ease of use: users can understand what is loaded without reading
  hidden runtime files.
- Explicit loading: users can clearly declare which skills, planspaces,
  or state plugs are loaded for a project or node.
- Portability: context can be cloned or moved across machines as an
  independent git repo.
- Low cognitive burden: state maintenance can be automatic once a plug
  is connected.
- Auditability: each node snapshots the exact context sources it saw.
- Parallel work: one code project can have several concurrent planning
  directions without overwriting one root-level `STATUS.md`.
- Safety: durable rules and skill definitions are not silently rewritten
  by ordinary worker nodes.
- Acceptance semantics: a node being done is distinct from its result
  being accepted.

## 3. Non-Goals

This is not a plan to replace MiniClaw2's `$MINICLAW_HOME/projects/...`
runtime store. The current JSON/JSONL store remains authoritative for
project, node, gate, and event state.

This is not a plan to run several agent nodes concurrently in the same
project worktree. Project-local execution should remain serialized.
Parallel work should use forks, worktrees, or isolated projects.

This is not a plan to inject every available context file. Loaded plugs
are selected, bounded, snapshotted, and visible to the user.

This is not a plan to import a whole ARIS or plugctx ecosystem. The
proposal borrows the proven shape: independent context repo, small
manifest files, explicit dependencies, and short prompt-facing context.

## 4. Filesystem Layout

### Project Repository

The project repository remains focused on code and code-reading
guidance.

```text
<project_root>/
  CONTEXT.md                 # codebase guidance only
```

`CONTEXT.md` explains the repository to an agent:

- architecture overview
- important directories
- build/test conventions
- durable code editing rules
- repo-specific gotchas

It should not contain:

- current project status
- active product plan
- branch/fork coordination
- transient blockers
- skill inventory
- long logs
- provider runtime state

This keeps the repository self-explanatory for any coding agent, even
outside MiniClaw2, while preventing root-level status files from
becoming branch-conflict magnets.

### ContextSpace Repository

Default location:

```text
$MINICLAW_HOME/contextspace/
```

This path should be configurable later, for example through
`MINICLAW_CONTEXT_HOME`, because advanced users may want to place the
context repo in Dropbox, iCloud, a dotfiles repo, or a manually managed
git checkout.

Suggested layout:

```text
$MINICLAW_CONTEXT_HOME/
  contextspace.yaml
  README.md

  plugs/
    global/
      CONTEXT.md
      manifest.yaml

    skills/
      <skill-id>/
        manifest.yaml
        CONTEXT.md
        assets/

    planspaces/
      <planspace-id>/
        manifest.yaml
        STATUS.md
        PLAN.md
        SKILLS.md
        events.jsonl
        inbox/
          <node-id>.memory-delta.json
        checkpoints/
          <timestamp-or-node>.json

    protocols/
      <protocol-id>/
        manifest.yaml
        CONTEXT.md

  bindings/
    projects/
      <binding-id>.yaml

  snapshots/
    <bundle-id>.json
```

This repository is git-maintained independently from any code project.
`STATUS.md`, `PLAN.md`, and `SKILLS.md` are tracked here when a planspace
plug exists. They are not created by default in a code project.

## 5. Plug Types

A plug is a reusable context object. It may provide prompt text,
structured state, a plan, or a list of child dependencies.

Initial plug types:

| Type | Purpose | Default Update Policy | Default Injection |
|---|---|---|---|
| `global` | user-wide behavior and MiniClaw2 conventions | manual/proposed | system or turn |
| `skill` | reusable tool/workflow knowledge | manual/proposed | turn |
| `planspace` | status, plan, loaded skill index, current direction | auto/semi-auto | turn |
| `protocol` | reusable execution loop or output contract | manual/proposed | turn |

Future plug types can include `dataset`, `remote`, `reviewer`,
`experiment-suite`, or `persona`, but v1 should not need them.

### Skill Plug

Example:

```text
plugs/skills/python-testing/
  manifest.yaml
  CONTEXT.md
  assets/
```

`CONTEXT.md` is short prompt-facing guidance. `assets/` may contain
longer references or scripts, but they are not injected by default.

### Planspace Plug

A planspace is the UX-visible planning and status surface. It is the
global version of the previous draft's root `STATUS.md`, `PLAN.md`, and
`SKILLS.md`.

```text
plugs/planspaces/miniclaw2-main/
  manifest.yaml
  STATUS.md
  PLAN.md
  SKILLS.md
  events.jsonl
  inbox/
  checkpoints/
```

`STATUS.md` is not default-created. It appears only when the user
creates or connects a planspace. Once present, it is tracked in the
ContextSpace git repo and may be automatically maintained by MiniClaw2.

`PLAN.md` is semi-automatic: planner nodes may propose updates, but
ordinary worker nodes should not silently rewrite the plan.

`SKILLS.md` is an index of loaded skills for this planspace, not a copy
of skill contents.

## 6. Manifests

### ContextSpace Manifest

```yaml
version: 1
kind: contextspace
name: default
created_by: miniclaw2
git:
  expected: true
defaults:
  context_budget:
    system_max_chars: 8000
    turn_max_chars: 6000
  auto_commit: false
```

`auto_commit` is intentionally false in v1. MiniClaw2 may write files,
but users should see git changes clearly. Later an op node can add
automatic ContextSpace commits.

### Plug Manifest

```yaml
version: 1
id: skills.python-testing
kind: skill
title: Python Testing
description: Short guidance for running and interpreting Python tests.
requires: []
tags: [python, testing]
write_policy: proposed
injection: turn
max_chars: 6000
```

For a planspace:

```yaml
version: 1
id: planspaces.miniclaw2-main
kind: planspace
title: MiniClaw2 Main Direction
description: Main planning and status track for MiniClaw2.
write_policy:
  STATUS.md: auto
  PLAN.md: proposed
  SKILLS.md: auto
  events.jsonl: auto
  inbox: auto
injection:
  STATUS.md: turn
  PLAN.md: turn
  SKILLS.md: none
max_chars:
  STATUS.md: 4000
  PLAN.md: 6000
```

Machine-readable routing, dependencies, injection, and write policy live
in manifests. Prompt-facing guidance lives in Markdown files.

## 7. Project Bindings

A binding connects one MiniClaw2 project to one or more plugs.

```text
bindings/projects/<binding-id>.yaml
```

Example:

```yaml
version: 1
id: project.miniclaw2.local-main
project:
  name: MiniClaw2
  miniclaw_project_id: null
  root_fingerprint:
    git_remote: git@github.com:user/MiniClaw2.git
    root_name: MiniClaw2
  local_paths:
    - /Users/bytedance/Desktop/repo/MiniClaw2
plugs:
  - id: global.default
    role: global-defaults
    injection: system
    enabled: true
  - id: planspaces.miniclaw2-main
    role: status-plan
    injection: turn
    enabled: true
    auto_update: true
  - id: skills.python-testing
    role: skill
    injection: turn
    enabled: true
```

Bindings should support many-to-many relationships:

- One project can bind several planspaces, for parallel directions.
- One planspace can bind several projects, for multi-repo work.
- One skill can bind many projects.
- A project fork can bind a new branch-specific planspace while still
  sharing global skills.

The binding is the object future UX should edit with drag-and-drop:
connect a plug to a project, disconnect it, change injection mode, or
disable it for a node.

## 8. Loading Semantics

At node launch, MiniClaw2 composes context from:

1. The project-root `CONTEXT.md`.
2. The project binding's enabled plugs.
3. Plug dependencies declared through `requires`.
4. Per-node overrides selected by the user.

The launch modal and node detail panel should make loaded plugs visible.
The user should be able to answer:

- Which planspace is attached?
- Which skills are loaded?
- Which files were injected?
- Which plug dependencies were pulled in?
- How much context budget was used?

This visibility is a core product requirement, not an implementation
detail.

## 9. Context Bundle Snapshot

Every node launch should produce a context bundle snapshot.

Proposed shape:

```json
{
  "bundle_id": "abc123",
  "created_at": 1234567890,
  "project_binding_id": "project.miniclaw2.local-main",
  "sources": [
    {
      "scope": "project-root",
      "kind": "code-guidance",
      "path": "/repo/MiniClaw2/CONTEXT.md",
      "sha256": "...",
      "chars": 1200,
      "injection": "system"
    },
    {
      "scope": "contextspace",
      "plug_id": "planspaces.miniclaw2-main",
      "kind": "status",
      "path": "plugs/planspaces/miniclaw2-main/STATUS.md",
      "sha256": "...",
      "chars": 900,
      "injection": "turn"
    },
    {
      "scope": "contextspace",
      "plug_id": "skills.python-testing",
      "kind": "skill",
      "path": "plugs/skills/python-testing/CONTEXT.md",
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

- Claude can receive project-root `CONTEXT.md` and global stable
  context through `system_prompt.append`.
- Codex can receive stable context on fresh thread start as today.
- Planspace status, plan, and skills should be included in launch/turn
  text so resumed provider threads can still receive current state when
  appropriate.

The exact snapshot should be persisted with the node or under
`ContextSpace/snapshots/<bundle-id>.json`, and the node should record
the `bundle_id`.

## 10. Automatic State Maintenance

Automatic state maintenance belongs to planspace plugs.

Nodes may emit memory deltas:

```text
plugs/planspaces/<planspace-id>/inbox/<node-id>.memory-delta.json
```

Shape:

```json
{
  "version": 1,
  "node_id": "<node id>",
  "project_id": "<project id>",
  "binding_id": "<binding id>",
  "created_at": 1234567890,
  "terminal_state": "done",
  "acceptance_state": "unreviewed",
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
      "target": "PLAN.md",
      "operation": "propose_patch",
      "policy": "proposed",
      "reason": "The next milestone changed after implementation.",
      "patch": "..."
    }
  ]
}
```

Application rules:

- `STATUS.md` updates can be auto-applied after terminal transitions,
  gate resolution, verifier results, or accepted memory deltas.
- `PLAN.md` updates are proposed by default and should require planner
  approval, human approval, or a future maintainer node.
- Skill and protocol plugs are proposed/manual by default.
- Failed or cancelled nodes can still generate observations, but the
  status must label them as failed/cancelled and should not promote them
  into durable rules.
- Rejected review gates should prevent the source node's durable memory
  proposals from being applied.

Because `STATUS.md` lives in ContextSpace, it can be tracked by the
ContextSpace git repo without causing code-branch conflicts.

## 11. Done vs Accepted

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

This distinction drives memory promotion. A completed node can update a
planspace `STATUS.md` as "done but unreviewed"; it should not update
skills, protocols, or durable rules until accepted.

## 12. Parallel Directions

One MiniClaw2 project can have multiple parallel directions by attaching
multiple planspaces. At launch time, however, each node must select
exactly one active planspace. That active planspace is the only
planspace whose `STATUS.md` and `PLAN.md` contribute to the node's
context bundle.

Example:

```yaml
plugs:
  - id: planspaces.miniclaw2-memory-protocol
    role: direction
    enabled: true
  - id: planspaces.miniclaw2-provider-parity
    role: direction
    enabled: false
```

The active node launch selects exactly one enabled direction plug.
Multiple skill or protocol plugs may still be loaded alongside it.

This solves the "one project, multiple parallel plans" problem without
forcing multiple `PLAN.md` files into the code repository. It also lets
the same code project keep separate status for:

- mainline implementation
- speculative redesign
- benchmark/evaluation track
- documentation track
- release hardening track

For project forks or worktrees:

- Forks may inherit global skills.
- Forks should usually get their own planspace plug.
- Merge should import fork memory deltas into the parent planspace inbox,
  not text-merge `STATUS.md` or `PLAN.md` blindly.

## 13. Migration and Multi-Machine Use

ContextSpace should be easy to migrate.

Recommended workflow:

1. User initializes or clones `$MINICLAW_CONTEXT_HOME` as a git repo.
2. MiniClaw2 reads `contextspace.yaml`.
3. MiniClaw2 scans bindings and tries to match projects by root
   fingerprint: git remote, root name, and optional local paths.
4. If paths differ on a new machine, the UI asks the user to reconnect
   a binding to the local project path.
5. Once reconnected, the same plugs and planspaces load.

Local machine paths should be treated as mounting hints, not identity.
The binding's stable identity should come from project metadata such as
repo remote, root name, and user-assigned project alias.

## 14. Suggested First Implementation Slice

The first slice should be small and should prove the data model.

1. Add `Node` acceptance fields while keeping `NodeState` unchanged.
2. Add a configurable ContextSpace root:
   `$MINICLAW_CONTEXT_HOME` or `$MINICLAW_HOME/contextspace`.
3. Add minimal plug and binding loaders for:
   - project-root `CONTEXT.md`
   - `plugs/planspaces/<id>/STATUS.md`
   - `plugs/planspaces/<id>/PLAN.md`
   - `plugs/skills/<id>/CONTEXT.md`
4. Add `project_context_binding_id` to `Project` or store it in
   `Project.settings_override` for the first slice.
5. Add context bundle snapshots with source paths, hashes, plug ids,
   and injection modes.
6. Continue populating `system_context_snapshot` for backward
   compatibility from project-root `CONTEXT.md`.
7. Add planspace `STATUS.md` auto-writer, but only when the project has
   an explicit planspace binding. Do not create it by default.
8. Add memory delta inbox support, but auto-apply only `STATUS.md`
   updates.
9. Update passive gate resolution so a gate can write acceptance fields
   on its source node.
10. Surface loaded plugs and context bundle sources in the node detail UI.

Defer:

- Drag-and-drop plug UX.
- Full skill authoring UI.
- Cross-provider reviewer nodes.
- Fork merge UI.
- Automatic edits to `PLAN.md`, skills, or protocols.
- Automatic ContextSpace git commits.
- Marketplace or bundled skill distribution.

## 15. Decisions Captured

This draft assumes:

- Project-root `CONTEXT.md` remains, but only for codebase guidance.
- `STATUS.md`, `PLAN.md`, and `SKILLS.md` live in ContextSpace
  planspace plugs, not in project roots.
- `STATUS.md` is not default-created. Once a planspace exists, it is
  tracked in the independent ContextSpace git repo.
- The context system is global and separable from MiniClaw2 projects.
- Project-context relationships are editable bindings, not hardcoded
  file paths.
- One project may connect to several planspaces for parallel directions.
- A node launch always selects exactly one active planspace. Multiple
  planspaces may be bound to the project, but only one contributes
  `STATUS.md` and `PLAN.md` to a node's context bundle.
- Users must be able to see and explicitly control which plugs are
  loaded.
- MiniClaw2 may automatically maintain state inside connected
  planspaces to reduce user cognitive burden.
- `done` vs `accepted` should enter the domain model.
- ContextSpace should not be silently initialized on first run. The user
  should create or select a ContextSpace when first using the feature.
- v1 should not automatically commit ContextSpace changes. Git diffs
  should remain visible to the user; a ContextSpace commit op can come
  later.
- v1 should keep project bindings only in ContextSpace, without writing
  a project-local pointer file.
- Deterministic verifier results should be represented as verifier op
  nodes. The verifier op owns its own execution record and writes the
  acceptance verdict back to the source node.

## 16. Remaining Discussion Areas

The major architectural questions above are settled for v1. Remaining
discussion can focus on concrete product and implementation details:

1. What does the first ContextSpace creation flow look like in the UI?
2. How should MiniClaw2 name new planspaces and bindings by default?
3. Which plug fields must be editable in the first UI surface, and which
   can stay YAML-only?
4. How much of `PLAN.md` should be injected into worker nodes versus
   planner/review nodes?
5. What exact verifier op schema should represent deterministic
   acceptance?
