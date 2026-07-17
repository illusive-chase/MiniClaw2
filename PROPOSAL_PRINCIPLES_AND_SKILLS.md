# Proposal: Principles & Agent Skills

Status: implemented (2026-07-17). Supersedes
`PROPOSAL_SKILLS.md` (whose mechanism landed and is renamed here).

Companion to `PHILOSOPHY.md` §7 (ContextSpace) and
`IMPLEMENTATION_STATUS.md`. When this document and the code disagree,
this document is the position to argue from until it is accepted or
revised.


## 1. Motivation

The first skills iteration (`PROPOSAL_SKILLS.md`, landed) built a
provider-neutral injection mechanism: a "skill" is a
`manifest.yaml` + `CONTEXT.md` whose text is eagerly composed into a
node's launch bundle. That mechanism is sound, but it conflates two
different things a user wants to attach to a node:

- **Behavior shaping** — how the agent should act: coding
  principles, review discipline, tone, house style. This wants to be
  *always in context*, is pure prose, and composes — several units
  together form the agent's working persona, the way an `AGENTS.md`
  does. Eager injection is exactly right for this.
- **Capabilities** — what the agent can do: a PDF toolchain, a
  release playbook with scripts, a framework guide with reference
  files. This wants *lazy loading* (metadata up front, body read on
  demand), may carry scripts and assets, and now has an
  industry-standard format — `SKILL.md` (the Agent Skills standard)
  — natively understood by **both** of our providers (Claude Code
  from the start; Codex since Dec 2025).

Forcing capabilities through eager text injection wastes context,
cannot carry scripts or assets, and reimplements what the vendors now
do better natively. Forcing behavior shaping into `SKILL.md` would
make it lazily loaded — exactly wrong for principles that must always
apply.

So: split.

1. The existing injection mechanism is **kept and renamed** to
   **principles**. Markdown only. The user composes several
   principles into a node's effective persona — an ephemeral,
   provider-neutral `AGENTS.md` assembled per launch.
2. A new, much thinner mechanism wraps **agent skills** in their
   native standard format. MiniClaw2 stores them **unmodified** (so
   community skills can be imported directly), selects them per node,
   materializes them into each provider's native discovery path, and
   displays them on the canvas. MiniClaw2 never parses or injects a
   skill body; loading is the provider's job.

The canvas metaphor is shared: both are user-wide tiles on the
context shelf, both attach to a node by drag (or panel selection),
both surface as edges. Their semantics differ and the visuals should
say so (§6).


## 2. Terminology

- **Principle** — a reusable, user-wide behavior-shaping context
  object. A slug, a `manifest.yaml`, and a single `CONTEXT.md`.
  Markdown only — no scripts, no assets. Injected eagerly at launch
  via the existing bundle composition. (This is the artifact formerly
  called "skill" in MiniClaw2.)
- **Agent skill** (or just **skill**) — a directory in the Agent
  Skills standard format: `SKILL.md` (frontmatter `name`,
  `description`; body = instructions) plus optional scripts and
  reference files. Stored verbatim; loaded by the provider's native
  skill mechanism. Never injected by MiniClaw2.
- **Shelf** — the visual library on the canvas context stripe.
  Now holds two visually distinct tile kinds.
- **Materialization** — the per-launch step that makes the selected
  skills discoverable to the spawned provider process (§5).
- **Available vs used** — an attached skill is *available*; whether
  the model actually loaded its body is *observed* from the event
  stream and rendered separately (§6.3).

Naming note: "principle" was chosen over "personality" because the
units compose — a shelf of principles reads naturally, whereas a
shelf of personalities implies picking one — and because the content
is often rules ("tests first", "terse commits"), not only tone. The
choice is a find/replace if revisited; see §12.


## 3. Principles (rename of the landed mechanism)

### 3.1 Semantics: unchanged

Everything that landed from `PROPOSAL_SKILLS.md` §3–§5 continues to
work identically — per-node opt-in via the settings snapshot,
dedupe against binding-provided plugs, `source: "node-opt-in"`
provenance, `missing: true` audit, `max_chars` truncation, the
`principle_edit` (né `skill_edit`) authoring concierge, promotion of
virtual intent at launch. Only names change.

### 3.2 Rename table

| current (landed)                          | new                                  |
| ----------------------------------------- | ------------------------------------ |
| `plugs/skills/<slug>/`                     | `plugs/principles/<slug>/`           |
| plug id `skills.<slug>`                    | `principles.<slug>`                  |
| `_plug_kind` → `"skill"`                   | `"principle"`                        |
| `settings_snapshot["extra_skills"]`        | `settings_snapshot["extra_principles"]` |
| `Node.pending_extra_skills`                | `Node.pending_extra_principles`      |
| `user_message.extra_skills`                | `user_message.extra_principles`      |
| `GET /skills`, `DELETE /skills/{slug}`     | `GET /principles`, `DELETE /principles/{slug}` |
| `agent_op_kind = "skill_edit"`             | `"principle_edit"`                   |
| `prompts/skill_init.md`                    | `prompts/principle_init.md`          |
| `normalize_skill_ids`                      | `normalize_principle_ids`            |
| frontend `SkillSummary` etc.               | `PrincipleSummary` etc.              |

The freed `skills.*` id namespace, `/skills` REST prefix, and
`extra_skills` / `pending_extra_skills` field names are reassigned to
agent skills (§4). The store migration (§8) makes this handover safe.

### 3.3 Markdown only

Principles are pure text. The plug directory spec drops `assets/`;
`manifest.yaml` + `CONTEXT.md` are the only recognized files. The
loader already reads nothing else, so this is a documentation-level
tightening plus a check in the authoring prompt.

### 3.4 Injection default

Stays `turn` (the current code default in
`_load_context_markdown_source`), because the two providers are only
symmetric on the turn channel: Claude re-applies `system` text on
every spawn via `--append-system-prompt`, while the Codex adapter can
only fake a system channel by prepending to the *first turn of a
fresh thread* — on resumed threads, `system`-injected text silently
drops. `turn` re-asserts principles on every node, which is what
behavior shaping wants anyway. The per-plug `injection` override
remains for power users, with this caveat documented in the manifest
comment.

`PROPOSAL_SKILLS.md` §3.1 claimed a `system` default; the code's
`turn` default is correct and this document adopts it.

### 3.5 Relation to on-disk AGENTS.md / CLAUDE.md

Unchanged and untouched: vendor config files in the repository
(`CLAUDE.md`, `AGENTS.md`, `.claude/`, `.codex/`) are applied by the
native binaries themselves. Principles are the provider-neutral,
graph-visible, per-node-selectable counterpart; they do not write to
or read from those files.


## 4. Agent skills (native)

### 4.1 On-disk library

```
$MINICLAW_HOME/contextspace/skills/<slug>/
  SKILL.md            # standard: frontmatter name+description, body
  ...                 # anything else the skill ships (scripts, refs)
```

Note: **not** under `plugs/`. Skills are not plugs — they do not
participate in bundle composition or (v1) bindings. `contextspace/`
remains the sync root, so skills ride ordinary metadata sync commits.
Caveat for the import UI: large binary payloads bloat the sync repo;
skills are expected to be markdown + small scripts.

The directory is stored **verbatim**. No MiniClaw2 manifest, no
format adaptation: a community skill (skills.sh, `$skill-installer`,
copied from any repo) drops in unmodified. `GET /skills` parses
`SKILL.md` frontmatter for `name` / `description`; the slug is the
directory name.

### 4.2 Attach model

Identical plumbing to principles, reusing the freed names:

- `user_message.extra_skills: list[str] | None` — skill ids
  (`skills.<slug>`) or bare slugs, normalized like principle ids.
- `Node.pending_extra_skills` — virtual intent, promoted into
  `settings_snapshot["extra_skills"]` at launch.
- Dedupe within the list; unknown slugs are recorded on the launch
  settings as missing (visible in the panel), never fatal.

No injection modes, no `max_chars`, no composition — the list is an
input to materialization only.

### 4.3 Per-launch materialization — Claude

The native CLI supports transient, per-invocation plugin loading.
Per launch, build an ephemeral skills-directory plugin under the
node's temp workspace:

```
<workspace>/skill-plugin/
  skills/
    <slug>/           # copied from the library
```

and append one flag in `build_argv` (`claude_native/spawn.py`):

```
--plugin-dir <workspace>/skill-plugin
```

Properties: per-node scoping (argv is per spawn — concurrent nodes
with different skill sets cannot interfere), works on resume (the
flag rides every spawn), zero mutation of `~/.claude` or the
project's `.claude/`. Cleanup joins the existing workspace reaping.

Copy rather than symlink until the plugin loader is verified to
follow symlinks; skills are small.

### 4.4 Per-launch materialization — Codex

Codex reads skills natively (`.agents/skills` scanned from cwd to
repo root; `~/.codex/skills`; progressive disclosure with an 8k-char
metadata budget) but exposes no per-invocation CLI flag. The completed
spike found a better process-scoped protocol path in codex-cli 0.144.1:
after `initialize`, app-server accepts `skills/extraRoots/set` with a
list of absolute skill directories. MiniClaw2 gives that call the exact
selected library directories before `thread/start` or `thread/resume`.

Each node already owns a private `codex app-server` process
(`providers/codex.py`), so the roots remain node-scoped across concurrent
runs without copying auth/config, touching the project worktree, or mutating
shared `~/.codex`. A live protocol probe confirmed the configured skill in
the subsequent `skills/list` response. If the RPC is unavailable or fails,
the node still launches without the selected skills and its audit entries are
marked failed.

### 4.5 Suggest mode

Native loading is lazy: attaching a skill makes it *available*; the
model decides from the frontmatter `description` whether to read the
body. A user who drags a skill onto a node may expect stronger
intent. Per-attach toggle:

- **available** (default) — materialize only.
- **suggest** — additionally append one line to the launch
  instructions: `The skill "<name>" is available and likely relevant
  to this task.` Plain turn text; provider-neutral; restores
  determinism without reinventing injection.

Carried next to the id, e.g.
`settings_snapshot["extra_skills"] = [{"id": ..., "suggest": bool}]`
(exact encoding decided at implementation; keep the wire shape
forward-compatible with a plain string list).

### 4.6 Audit

The bundle snapshot no longer freezes injected text for skills (there
is none). Instead, the launch settings record, per attached skill:
id, resolved path, a content hash of the skill directory at launch
time (skills mutate between runs), the materialization mechanism
used, and missing/failed flags. This is the graph's answer to "what
exactly did this node have access to".

### 4.7 Import

`POST /skills/import` — from a local path, git URL, or zip. Import
is the **only** write path (besides delete); there is no authoring
concierge for skills in v1 (import-first; see §11). Safety
review/scanning of imported skills is deliberately deferred to the
import mechanism's own implementation — the library only ever holds
what the user explicitly imported, and skills are inspectable in the
panel before first attach (§6.2).


## 5. Runner changes

- Bundle composition: unchanged for principles (rename only). Skills
  never enter `compose_context_bundle`.
- New pre-launch step in `_run_agent`, after
  `_snapshot_context_bundle` and before provider start: resolve
  `settings_snapshot["extra_skills"]`, materialize per provider
  (§4.3/§4.4), write the audit record (§4.6), and hand the
  materialization handle (plugin dir path / env overrides) to the
  provider context.
- `AgentProviderContext` gains a small optional
  `skill_materialization` field; each adapter consumes its own shape
  (argv flag for Claude, env/params for Codex).
- Suggest-mode lines (§4.5) append to `_compose_launch_instructions`
  after the category block.
- Reap: clean ephemeral materialization dirs with the workspace.


## 6. Canvas / UX

### 6.1 Shelf

Two tile kinds on the context stripe, visually distinct (icon +
color; skills get their own chrome rather than reusing the context
tile look):

- **Principle tiles** — exactly today's behavior: dimmed when no
  live node loads them, full when loaded, attached-count badge,
  drag-onto-virtual, `loads` edge when actually injected.
- **Skill tiles** — same interactions (drag, badge, panel select),
  different edge semantics (§6.3).

If the shelf grows past comfortable width, group by kind with
collapsible sections; deferred until it hurts.

### 6.2 Panels

`ContextNodePanel` splits its skill case in two:

- **Principle** — as today: description, injection mode, content
  preview, two-step delete.
- **Skill** — frontmatter name/description, provenance (import
  source + date), a file listing of the skill directory, `SKILL.md`
  body preview, two-step delete. Inspectability before first attach
  is the v1 stand-in for import-time safety checks.

`AgentPanel`'s attach section becomes two lists (principles, skills)
with the same chips + dropdown interaction; skill chips carry the
suggest toggle.

### 6.3 Edges

- Principle → node: `loads` (solid), unchanged — injection is a fact
  of the launch.
- Skill → node: **available** (dashed) derived from the launch
  settings; upgraded to **used** (solid) when skill invocation is
  observed in the event stream — the Skill tool call in Claude's
  transcript, or a read of the materialized `SKILL.md` path in Codex
  activity. Detection is conservative: only upgrade on a confident
  match; a missed detection leaves a truthful "available".

This makes the canvas say something the injection design could not:
whether the skill actually influenced the run.


## 7. REST & wire summary

New / renamed REST:

```
GET    /principles                     (rename of GET /skills)
DELETE /principles/{slug}              (rename)
GET    /skills                         (native skills; parses SKILL.md)
POST   /skills/import                  (local path | git URL | zip)
DELETE /skills/{slug}
```

Client → server:

- `user_message.extra_principles` — renamed from `extra_skills`.
- `user_message.extra_skills` — native skills (id list; optionally
  the suggest-carrying shape of §4.5).
- `user_message.agent_op_kind` — value renamed to
  `"principle_edit"`.
- Virtual create/edit accepts `pending_extra_principles` and
  `pending_extra_skills`.

Server → client: no new event types. `node_started` /
`node_updated` payloads expose both attach lists; skill-used
detection rides existing `activity` events (frontend-side matching
in v1; a dedicated `skill_used` event only if matching proves
unreliable).


## 8. Migration (implemented as store schema v5 → v6)

The repository had already assigned v5 to the canonical model/artifact schema
before this proposal landed, so implementation uses v6. The migration content
below is otherwise unchanged.

One migration, one PR, applied on first startup of the upgraded
process (sync once before and after upgrading, per README):

1. Move `contextspace/plugs/skills/*` → `contextspace/plugs/principles/*`.
2. Rewrite ids `skills.<slug>` → `principles.<slug>` in:
   - binding YAMLs (`bindings/projects/*.yaml`),
   - `node.json` `settings_snapshot["extra_skills"]` →
     `["extra_principles"]` (keeps rerun working on old nodes),
   - live virtuals' `pending_extra_skills` →
     `pending_extra_principles`,
   - template YAMLs, if any reference skill plugs (grep during
     implementation).
3. Leave bundle snapshots (`snapshots/*.json`) untouched — they are
   immutable audit records of what actually launched.
4. Bump `schema.json`; `KNOWN_AGENT_OP_KINDS` accepts
   `principle_edit`, and the migration rewrites stored
   `agent_op_kind: "skill_edit"` → `"principle_edit"`.

Failure mode if a rewrite is missed: a stale `skills.<slug>` entry
resolves against the new native-skill library, fails, and surfaces
as a missing-skill flag on the next launch — visible, not silent
misinjection.


## 9. File-by-file change list

Backend:

- `domain.py` — rename `pending_extra_skills` →
  `pending_extra_principles`; add `pending_extra_skills` (native);
  whitelist `principle_edit`; invariants for both lists.
- `contextspace.py` — rename plug kind/dirs/normalizers; principles
  keep the composition path; new `skills.py`-worthy helpers may stay
  here or split: `list_agent_skills`, `import_skill`,
  `delete_agent_skill`, `skill_content_hash`.
- `events.py` — `extra_principles` + new `extra_skills` on
  `UserMessage`.
- `registry.py` — parameter renames; promotion handles both lists.
- `runner.py` — materialization step (§5); suggest lines; audit
  record.
- `providers/base.py` — `skill_materialization` on context.
- `providers/claude_native/spawn.py` — `--plugin-dir`.
- `providers/codex.py` — outcome of the §4.4 spike.
- `app.py` — REST renames + `/skills` + `/skills/import`.
- `store.py` / migration module — v4→v5 (§8).
- `prompts/principle_init.md` — rename + markdown-only tightening.

Frontend:

- `api.ts` / `types.ts` — `PrincipleSummary`, new `SkillSummary`
  (frontmatter shape), import/delete helpers, field renames.
- `canvas/layout.ts` — aggregate both kinds; skill tiles get their
  own node data kind; available/used edge derivation.
- `canvas/nodes/` — skill tile chrome (new or variant of
  `ContextNode`).
- `panel/ContextNodePanel.tsx` — split principle/skill cases (§6.2).
- `panel/AgentPanel.tsx` — two attach sections; suggest toggle.
- `App.tsx` — fetch both libraries; drop handlers for both tile
  kinds.


## 10. Staging

Four PRs, each independently shippable:

1. **Rename migration.** §3 + §8 + all field/REST renames, no
   behavior change. Green tests prove the handover is complete.
2. **Skill library.** On-disk root, `GET /skills` +
   `POST /skills/import` + `DELETE`, shelf tiles + panel
   (read-only). No launch integration yet.
3. **Claude materialization.** Attach flow end-to-end on the Claude
   provider: `--plugin-dir`, audit record, available edge, suggest
   toggle.
4. **Codex materialization + used-edge.** After the §4.4 spike:
   Codex path, activity-based used detection for both providers.


## 11. Non-goals and deferrals

- **Import-time safety scanning.** Deferred to the import
  mechanism's implementation. The library holds only deliberately
  imported skills; panel inspectability (§6.2) is the interim
  control.
- **Skill authoring concierge.** Import-first. A `skill_edit`-style
  concierge that authors `SKILL.md` can return later if hand-rolled
  skills become common.
- **Project-bound skills.** v1 skills are per-node opt-in only;
  binding-level "always available in this project" is a later,
  additive step.
- **Marketplace / registry browsing.** Import takes a path/URL; no
  in-app catalog.
- **Skill versioning / update tracking.** Re-import overwrites; the
  per-launch content hash (§4.6) is the audit trail.
- **Vendor-config skills.** Skills the native binaries already load
  from the repo or user config (`.claude/skills`, `~/.codex/skills`)
  remain invisible to the canvas, like the rest of vendor config.
  Rendering them as read-only tiles is a separate proposal if wanted.
- **Principle ordering UI.** Composition order is binding order then
  opt-in order, as today. An explicit reorder control is deferred.


## 12. Implementation decisions

- **Name:** `principle` / `principles.*`.
- **Codex mechanism:** per-node app-server `skills/extraRoots/set`, passing the
  exact selected skill directories before thread start/resume. This protocol
  was verified against codex-cli 0.144.1 and avoids shared config or auth
  mutation. Older/failed app-server calls degrade to a launch without skills
  and mark the audit entries failed.
- **Claude materialization:** copy into the ephemeral plugin directory.
- **Suggest encoding:** structured `{id, suggest}` entries; string input remains
  accepted and normalized for wire compatibility.
- **Used-edge detection:** conservative backend activity matching persisted in
  the launch audit. Claude requires a matching `Skill` invocation; Codex
  requires a read/command containing the selected materialized `SKILL.md` path.
