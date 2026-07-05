# Proposal: Skills

Status: proposal (not yet implemented). Draft.

Companion to `PHILOSOPHY.md` §7 (ContextSpace) and
`IMPLEMENTATION_STATUS.md`. When this document and the code disagree,
this document is the position to argue from until it is accepted or
revised.


## 1. Motivation

The `skill` plug kind is already reserved in the ContextSpace model.
`plugs/skills/<slug>/` is a first-class path in
`backend/miniclaw2/contextspace.py` (`_plug_dir`, `_plug_kind`),
`compose_context_bundle` already treats a bound skill plug identically
to a `global` plug — reads `CONTEXT.md`, honors `injection`,
respects `max_chars`, appends into the bundle. The loading path works.

What is missing:

- No way to **create** a skill (no manifest writer, no UI entrypoint).
- No way to **activate** a skill (no shelf, no drag affordance, no
  per-node opt-in mechanism).
- No **audit distinction** between a skill loaded by binding and a
  skill loaded ad-hoc for a single node.
- No **canvas presence** for skills, so a user has no way to see what
  skills exist or which ones a node has pulled in.

`extra_planspace_loads` is on the wire but never consumed downstream;
it is dead code and should be removed as part of this work rather
than repurposed.


## 2. Terminology

- **Skill** — a reusable, user-wide context object. A slug, a
  `manifest.yaml`, and a `CONTEXT.md`. No per-project state. One
  skill can be attached to any number of node launches across any
  number of projects.
- **Skill shelf** — the visual library of all known skills on the
  canvas. Reuses the existing loaded-context stripe at
  `LANE.contextLaneY`.
- **Skill-edit node** — an agent node whose job is to author a skill
  (create or refine `manifest.yaml` + `CONTEXT.md`). Marked with
  `agent_op_kind = "skill_edit"`. Otherwise a regular agent turn.
- **Per-node opt-in** — the loading model. Skills are not bound to a
  project. They are attached to a specific node's launch (or a
  virtual's `pending_extra_skills`, promoted at launch).


## 3. Data model

### 3.1 On-disk (unchanged)

```
$MINICLAW_HOME/contextspace/plugs/skills/<slug>/
  manifest.yaml
  CONTEXT.md
```

`manifest.yaml` schema (minimal; existing fields honored by
`_plug_manifest` / `_max_chars_for` / `_injection_for` continue to
work):

```yaml
version: 1
kind: skill
id: skills.<slug>
title: <string>
description: <string>          # optional; used by the shelf panel
injection: system | turn        # default: system (per-file override still allowed)
max_chars: <int>                # optional; existing default 6000
created_at: <float>             # unix time
```

No `binding` field. Skills are user-wide by construction.

### 3.2 `Node` schema additions (`backend/miniclaw2/domain.py`)

Two new fields on `Node`:

```python
class Node(BaseModel):
    ...
    agent_op_kind: str | None = None       # NEW: marks concierge / preset agent variants
    pending_extra_skills: list[str] = Field(default_factory=list)  # NEW
```

Invariants (enforced in `_check_invariants`):

- `agent_op_kind` is only valid when `kind == AGENT`. For `OP` or
  `VERIFIER`, must be `None`.
- Known values: `"skill_edit"`. Unknown non-None values are rejected
  at write. (Kept as `str | None` rather than a `StrEnum` so new
  variants can be added without a domain-schema migration; the
  runner and validator hold the whitelist.)
- `pending_extra_skills` is only meaningful for AGENT nodes.
  VERIFIER / OP must leave it empty. No further validation on
  contents at model level; missing / stale skill ids are resolved
  best-effort at compose time (see §4.2).

### 3.3 `PlugRef.source` (`contextspace.py`)

Existing values: `"binding"`, `"requires:<upstream>"`.

New value: **`"node-opt-in"`** — the skill was pulled in via a
node's `extra_skills`, not via a binding. This surfaces in
`context_sources` so the UI can show *why* a skill loaded.

### 3.4 `Project` schema

Unchanged. Skills do not participate in project bindings.


## 4. Loading semantics

### 4.1 Wire protocol

`events.py` `UserMessage`:

```python
class UserMessage(BaseModel):
    type: Literal["user_message"]
    text: str
    resume_from_node_id: str | None = None
    extra_skills: list[str] | None = None   # NEW
    # extra_planspace_loads: REMOVED (see §10)
```

`user_message.extra_skills` carries a list of skill plug ids
(`"skills.<slug>"`) or bare slugs (`"<slug>"`); the backend
normalizes both to `"skills.<slug>"`.

### 4.2 Composition

`compose_context_bundle` (`contextspace.py`) is extended after the
existing binding-driven plug loop:

1. Read `node.settings_snapshot.get("extra_skills", [])`.
2. For each entry, construct
   `PlugRef(id=..., source="node-opt-in", enabled=True)`.
3. Deduplicate against skills already loaded via binding
   (bindings only; the skill will not be double-loaded).
4. Resolve `_load_context_markdown_source(root, ref, "skill")` on
   each. Missing skill plugs are skipped silently and recorded in a
   new `sources[*]` audit field `missing: true` so the UI can flag
   the launch.
5. Injection follows the manifest as today: `system` → appended to
   `system_text`, `turn` → appended to `turn_text`, `none` → skipped.

`context_sources` on the node continues to record file paths;
`sources[*]` in the persisted bundle already carries `plug_id` and
now carries `source` so audits distinguish opt-in from binding.

### 4.3 Promotion

`Node.pending_extra_skills` is virtual-only intent. At promotion (in
`registry.start_node` or wherever the virtual → queued path lives):

- Copy `pending_extra_skills` into
  `settings_snapshot["extra_skills"]`.
- Clear `pending_extra_skills` on the promoted node.

Direct-launch nodes (phantom composer with skills attached at submit
time) skip this and put the list straight into
`settings_snapshot["extra_skills"]` via `start_node`'s new
`extra_skills` parameter.


## 5. Skill-edit node

### 5.1 Marker

An agent node with `agent_op_kind == "skill_edit"` behaves like any
other agent turn (fresh session, uses the project's worktree, obeys
inline gates) except:

- **Launch instructions.** The runner prepends the preset prompt at
  `backend/miniclaw2/prompts/skill_init.md`. The user's seed text
  becomes the user turn on top.
- **Target directory.** The prompt instructs the agent to write to
  `$MINICLAW_HOME/contextspace/plugs/skills/<slug>/` — absolute path
  outside the worktree. The runner must ensure the agent has write
  access to that directory; on Claude this is already the case with
  the native CLI adapter, since it inherits the ambient filesystem.
- **Lane preview.** The node still writes a lane preview per §8 of
  PHILOSOPHY. Content: "created skill `<slug>`" or "refined skill
  `<slug>`", pointing at the plug dir. This keeps graph consistency
  ("something happened here") without duplicating the skill's
  actual content into the preview.
- **`commit_before` / `commit_after`.** Move only if the agent
  incidentally touched the worktree. The skill's own files are not
  under version control by the project's git repo — they live in
  ContextSpace.

### 5.2 Preset prompt (`prompts/skill_init.md`)

New file. Modeled on `context_init.md` / `context_refresh.md`.
Enforces:

- Kebab-case slug, extracted from the user's intent or defaulted.
- Write exactly two files: `manifest.yaml` and `CONTEXT.md`.
- No writes outside `$MINICLAW_HOME/contextspace/plugs/skills/<slug>/`.
- Skill CONTEXT.md is skim-friendly reference — one screen preferred,
  not exhaustive.
- Skill CONTEXT.md is plan-free — no current-state, no TODOs, no
  timelines. (Matches the discipline in `context_init.md`.)
- If the slug already exists, the agent refines the existing files
  rather than overwriting blindly, and does not touch unrelated
  fields in `manifest.yaml`.

### 5.3 Context grounding is the node's cwd

The concierge agent has no toggle for "read this project's code."
The user controls context by choosing where to launch the skill-edit
node:

- Real project → agent sees code, can ground the skill in real
  examples.
- Temporary project (already supported by MiniClaw2) → agent works
  from the seed paragraph alone.

This eliminates a config knob and pushes the decision to the graph,
where the user already reasons about scope.


## 6. UX

### 6.1 Skills shelf

`layout.ts` enumerates all user-wide skills via `GET /skills` and
merges them into the ctx aggregate map alongside skills-loaded-by-a-
visible-node. Rendered on the existing loaded-context stripe
(`LANE.contextLaneY`).

Visual states on the shelf:

- **Dimmed** — the skill exists but no live node has loaded it.
  Discoverable but visually recessive.
- **Full** — the skill is loaded by at least one visible node. Same
  treatment as today's loaded-context tiles.
- **Attached badge** — the skill is pre-attached to a phantom or
  virtual currently visible. A small badge on the skill tile shows
  count.

`ContextNodePanel.plainLanguageDescription` gains a
`kind === "skill"` case: something like *"A reusable skill —
tool/workflow knowledge available to any node you attach it to."*
The panel also shows: manifest title/description, injection mode,
delete button (see §7).

### 6.2 Drag onto phantom

Phantom composers hold a local `Set<string>` of pre-attached skill
ids, rendered as small chips on the composer tile. Drag source: any
skill tile on the shelf. Drop target: the phantom.

On submit, the set becomes `user_message.extra_skills`.

Drag interaction uses React Flow's node-drag events plus a
collision check against phantom bounds. Phantoms are not backend
objects, so this is purely frontend state until submit.

### 6.3 Drag onto virtual

Same interaction; writes to the virtual's `pending_extra_skills`
via a new endpoint or via the existing virtual-edit path (whichever
is cheaper — see §7). Editing the virtual's chips (remove) uses the
same path.

### 6.4 "+ New skill"

A small affordance on the skill shelf. Clicking it opens a phantom
composer variant with:

- `agent_op_kind` pre-set to `"skill_edit"`.
- A seed textarea captioned "what does this skill teach?"
- A slug preview (auto-derived from the first line, editable).

On submit, this launches like any node — carries `agent_op_kind` in
the payload, which `start_node` writes to the created node.

### 6.5 Existing edges

Skills that a node has loaded already surface as `type: "loads"`
edges from the skill's context tile to the node (see
`layout.ts:672`). No change.


## 7. REST API

### 7.1 New

```
GET  /skills
     → [{ id, slug, title, description, injection, path, max_chars, exists }]

DELETE /skills/{slug}
     → 204 or { deleted: true }
```

`GET /skills` scans `plugs/skills/*/manifest.yaml`, returns a shape
compatible with `_plug_summary` (kind="skill", slug, title,
description, path, etc.). No pagination — skills are a small set.

### 7.2 Not added

- `POST /skills` — creation goes through the normal `user_message`
  path with `agent_op_kind: "skill_edit"`. There is no direct-write
  endpoint. This keeps skill authorship visible in the graph.
- `PATCH /skills/{slug}` — refinement is a resumed skill-edit node,
  not a REST endpoint.

### 7.3 Virtual updates

Pre-attaching skills to a virtual node needs a mutation path. Either:

- Extend the existing virtual-edit endpoint (whatever writes
  `prompt_draft` / `scheduled_deps` on virtuals) to accept
  `pending_extra_skills`.
- Add `PATCH /sessions/{sid}/nodes/{nid}/pending-skills`.

Prefer option (a) — one virtual-edit path, not two. Concrete file
to look at once we implement: whatever endpoint in `app.py` currently
writes `prompt_draft` on virtuals.


## 8. Wire protocol changes summary

Client → server:

- `user_message.extra_planspace_loads`: **removed**.
- `user_message.extra_skills: list[str] | None`: **added**.
- `user_message.agent_op_kind: str | None`: **added** — carries
  `"skill_edit"` for concierge launches.

Server → client:

- `NodeStarted` gains `agent_op_kind` (optional). Lets the canvas
  render skill-edit nodes with distinct chrome.
- No changes to event streams; skill-edit nodes emit the same
  `text_delta` / `activity` / `turn_done` events.

`interaction_response` unchanged.


## 9. Runner changes

`backend/miniclaw2/runner.py`:

- In the agent launch path (around `_run_agent` line ~140), branch
  when `self.node.agent_op_kind == "skill_edit"`:
  - Load the preset prompt from `prompts/skill_init.md`.
  - Compose launch instructions with the preset as an additional
    system block (before the standard category + language +
    anti-self-poisoning blocks).
  - The user's seed text (`self.node.prompt`) becomes the user
    turn on top of that.
- Snapshot / reap paths otherwise unchanged. The node still writes
  a lane preview; reap still validates non-planning nodes only
  write their own preview.

`registry.start_node` gains parameters:

- `extra_skills: list[str] | None`.
- `agent_op_kind: str | None`.

Both flow into the created `Node`. `extra_skills` also lands in
`settings_snapshot["extra_skills"]`.


## 10. Dead code removal

`extra_planspace_loads` never composes into any bundle; only the
wire and registry accept it, and the settings snapshot silently
carries it. Delete from:

- `backend/miniclaw2/events.py` — `UserMessage.extra_planspace_loads`.
- `backend/miniclaw2/registry.py` — parameter, normalizer,
  `settings_snapshot["extra_planspace_loads"]` write.
- `backend/miniclaw2/app.py` — the WS handler that forwards it.
- `frontend/src/types.ts` — the field on the `user_message` type.

Delete in the same PR as this proposal's changes to avoid a
half-alive parallel path.


## 11. File-by-file change list

Backend:

- `backend/miniclaw2/domain.py` — add `agent_op_kind`,
  `pending_extra_skills` to `Node`; extend `_check_invariants`.
- `backend/miniclaw2/contextspace.py` — add `list_skills`,
  `create_skill`, `delete_skill` helpers; extend
  `compose_context_bundle` to honor
  `settings_snapshot["extra_skills"]`; extend `PlugRef.source`
  usages.
- `backend/miniclaw2/events.py` — remove
  `extra_planspace_loads`; add `extra_skills`, `agent_op_kind` on
  `UserMessage`; extend `NodeStarted`.
- `backend/miniclaw2/registry.py` — remove
  `extra_planspace_loads`; accept `extra_skills` and
  `agent_op_kind`; write them into the node.
- `backend/miniclaw2/app.py` — REST handlers for
  `GET /skills`, `DELETE /skills/{slug}`; forward
  `extra_skills` and `agent_op_kind` from the WS payload; remove
  `extra_planspace_loads` forwarding; extend virtual-edit endpoint
  to accept `pending_extra_skills`.
- `backend/miniclaw2/runner.py` — branch on
  `agent_op_kind == "skill_edit"` at launch; load
  `prompts/skill_init.md`.
- `backend/miniclaw2/prompts/skill_init.md` — new preset.

Frontend:

- `frontend/src/api.ts` — `listSkills`, `deleteSkill` helpers;
  extend `user_message` sender.
- `frontend/src/types.ts` — `SkillSummary` type; remove
  `extra_planspace_loads`; add `extra_skills`, `agent_op_kind`.
- `frontend/src/canvas/layout.ts` — enumerate skills into the
  ctx aggregate; add dimmed state.
- `frontend/src/canvas/nodes/ContextNode.tsx` — dimmed variant,
  attached-badge count.
- `frontend/src/panel/ContextNodePanel.tsx` — `kind === "skill"`
  description; skill delete button.
- Phantom composer component — pre-attached skill chips, drag drop
  target, `agent_op_kind: "skill_edit"` variant with the "+ New
  skill" launch button.
- Virtual node tile — chips for `pending_extra_skills`, drag
  target.


## 12. Staging

Ship in four PRs, each independently valuable:

1. **Backend plumbing.** Domain fields, `list_skills` / `create_skill`
   / `delete_skill`, `extra_skills` bundle composition,
   `PlugRef.source="node-opt-in"`, delete `extra_planspace_loads`.
   Testable via curl and unit tests over `compose_context_bundle`.

2. **Skill-edit concierge.** `prompts/skill_init.md`, runner branch,
   REST wiring for `agent_op_kind`. Testable by launching a
   skill-edit node manually and inspecting the created plug dir.

3. **Skill shelf (read-only).** `GET /skills`, layout changes,
   dimmed tile state, panel description. Testable by hand-creating
   a skill on disk and confirming it appears.

4. **Drag + create UX.** Drag-onto-phantom, drag-onto-virtual,
   "+ New skill" button, chip rendering, virtual-edit endpoint
   extension. Testable end-to-end.


## 13. Non-goals and deferrals

- **Skill versioning.** No `version` history beyond the manifest
  field. If a skill's `CONTEXT.md` regresses, the user reruns a
  refine turn (skill-edit node with the same slug).
- **Automated skill discovery / retrieval.** No embeddings, no
  auto-selection. If the user wants a skill for a node, they attach
  it manually or a planning agent proposes it via
  `pending_extra_skills`.
- **Skill dependencies (`requires`).** The `requires` chain already
  works in the loader (`_expand_required_plugs`). Skills may
  declare requires in their manifests; no new UI to author them —
  power users hand-edit. Revisit if that gap actually hurts.
- **Cross-machine skill sharing.** Skills are user-wide by
  filesystem, not synced. Sync is a ContextSpace-level concern and
  out of scope here.
- **Skill-scoped tool allowlists.** Skills contribute markdown, not
  tools or settings. Vendor-specific tool config remains under
  `CLAUDE.md` / `.claude/settings.json` and applies via the native
  binary.


## 14. Open questions

- **Naming of `agent_op_kind`.** The field currently named
  `op_kind` on `Node` is reserved for `NodeKind.OP` nodes. Adding a
  parallel-name field is a small readability tax. Alternatives:
  `agent_variant`, `agent_marker`. Pick one before merging §11.
- **Skill delete confirmation.** User-wide artifacts should not be
  deleted silently. Two-step confirm on the panel is probably
  right; the exact UX is a small design call.
- **What happens if a skill is deleted while a node's saved bundle
  references it?** The bundle snapshot on disk is untouched (it's an
  audit record). The next launch simply cannot re-load it and
  records `missing: true` per §4.2.
