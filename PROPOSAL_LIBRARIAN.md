# The Librarian — Authoring Principles and Skills (2026-07)

A design proposal for a unified authoring concierge: one agent node
variant, `agent_op_kind = "library_edit"`, that authors or refines
either a *principle* or a *native Agent Skill* from a user seed. In
prose and UI it is **the librarian** — it maintains the user-wide
library of reusable context objects.

This un-defers an item recorded in `IMPLEMENTATION_STATUS.md`
("Deliberate v1 skill deferrals: … a skill-authoring concierge
(import-first — revisit if hand-rolled skills become common)") and
generalizes the existing principle-edit concierge rather than adding a
new concept: the node is an ordinary regular-category agent with a
special launch block, exactly the extension point `agent_op_kind` was
built for.


## 1. Current state

**Principle authoring exists and works.** `agent_op_kind =
"principle_edit"` (whitelisted in `domain.py`'s
`KNOWN_AGENT_OP_KINDS`) marks an ordinary agent node whose launch
instructions are prefixed with `prompts/principle_init.md`
(`runner.py` → `launch_prompt.build_principle_init_block`). The prompt
constrains writes to exactly `manifest.yaml` + `CONTEXT.md` under
`contextspace/plugs/principles/<slug>/`, teaches refine-means-merge,
and requires the normal lane preview. The frontend creates it as a
regular virtual node from Project → `+ New principle`
(`App.tsx: handleNewPrinciple`), and refreshes the principle shelf
when such a node reaches a terminal state. Enforcement is prompt
discipline only — nothing validates what was actually written.

**Skills are import-only.** Native Agent Skills (`SKILL.md` standard)
live verbatim under `contextspace/skills/<slug>/`. Import
(`skills.py`) validates frontmatter (non-empty `name`,
`description`), slug shape, and symlink absence; records provenance
in `contextspace/skill-imports.json`; and the runner audits per-launch
content hashes in `settings_snapshot.skill_audit`.

Two hazards shape this design:

1. **The string `skill_edit` is radioactive.** A store migration
   (`sync.py`) rewrites legacy `agent_op_kind: "skill_edit"` →
   `"principle_edit"`, from the era when principles were called
   skills. Any new op kind must avoid that name.
2. **Authored skills would skip all import validation.**
   `list_agent_skills` silently drops a skill directory whose
   `SKILL.md` fails inspection. An authoring agent that writes a
   malformed skill would report success while the skill invisibly
   never appears on the shelf. Validation of authored output must
   live somewhere.


## 2. Decisions

Settled in design discussion; recorded here with rationale.

### 2.1 One unified kind, agent-decided target

A single `library_edit` node authors **either** a principle **or** a
skill, deciding from the seed: a principle when the seed is
behavior-shaping guidance that should inject eagerly; a skill when it
is tool/workflow knowledge that should lazy-load via `SKILL.md`
frontmatter. A genuinely ambiguous seed opens the smallest inline
`ask_user` gate (same doctrine as `concierge_bootstrap.md`: defaults
the user can edit beat questions the user must answer).

The user should not need to pre-classify their idea; teaching the
principle/skill boundary is part of the librarian's job. No new Node
field is added for the target — the seed carries the intent, the
preview and audit record the outcome.

### 2.2 Naming: `library_edit` / "the librarian"

Two-layer naming follows the existing convention exactly: op kinds
are operation-style (`principle_edit`), personas live in prose
(`domain.py` calls principle_edit "the concierge that authors
principles"; `concierge_bootstrap.md` names the planspace
bootstrapper). So: `library_edit` in the whitelist, "the librarian"
in prompt title and docs.

"Library" is already the codebase's word for this collection —
`skills.py` names the skills directory `library`, and the README
groups "Principles and Agent Skills" as one user-wide concept.

Rejected: `skill_edit` (migration collision), `context_edit`
(collides with the `context_refresh` CONTEXT.md machinery),
`concierge` (taken), `guide` (placeholder; collides with "guidance"
prose), `knowledge_edit` (no codebase anchor), `distill` (implies
extraction-only), `curator` (curators arrange existing works; this
agent writes new ones).

### 2.3 Coexistence with `principle_edit`

`principle_edit` stays in the whitelist untouched: historical nodes
render, replay, and rerun exactly as today. No store migration, no
`sync.py` change. The librarian becomes the only *creation* entry
point in the UI.

Manual editing is unaffected and remains first-class: principles and
skills are plain files under `$MINICLAW_HOME/contextspace/`, the
shelves re-list from disk on every fetch, and metadata sync commits
hand edits like anything else. The librarian's refine mode is
read-first/merge, so it does not clobber hand edits. The separate
pending item of an in-app manifest/markdown editor stays pending and
orthogonal.

### 2.4 Full skill trees from day one

The librarian may write scripts, references, and assets inside a
skill, like imports may contain them (minus symlinks). The trust
story is identical to imports: nothing executes at authoring time,
panel inspectability before first attach is the control, and the
per-launch content hash in `skill_audit` is the audit trail.

### 2.5 Validation: fail hard

When a librarian turn finishes, the runner validates what was
actually written and errors the node on any failure (no repair
loop; the existing rerun flow is the retry path). This closes the
silent-drop hazard (§1.2). Details in §3.3.


## 3. Design

### 3.1 Ontology

- `KNOWN_AGENT_OP_KINDS = frozenset({"principle_edit", "library_edit"})`
  (`domain.py`). No other schema change; the whitelist is a plain set
  precisely so this needs no migration.
- A librarian node is `kind=agent`, `category=regular`, lives in the
  active lane, and uses the full normal lifecycle: virtual → promote →
  queued → running → terminal, preview contract, reap, rerun,
  interrupt, replay. Native-machine gating (`require_native`) applies
  as usual.

### 3.2 Launch composition

New template `prompts/library_init.md` (title: "# MiniClaw2 —
librarian"), substituting `<<principles_dir>>` and `<<skills_dir>>`
(both absolute contextspace paths). `launch_prompt.py` gains
`build_library_init_block(principles_dir, skills_dir)`;
`runner._principle_init_block` generalizes to an authoring-block
dispatcher on `agent_op_kind`, occupying the same first slot in
`_compose_launch_instructions`.

The prompt teaches:

- **The decision boundary.** Principle = eagerly injected
  behavior-shaping guidance; two files, markdown only, one screen of
  skim-friendly reference. Skill = `SKILL.md`-standard lazy-loaded
  tool/workflow knowledge; frontmatter `name` + `description` written
  so a provider can decide relevance without loading the body; may
  include `references/`, `scripts/`, assets. Ambiguous seed → smallest
  inline `ask_user` gate.
- **One entry per run.** Create or refine exactly one principle *or*
  one skill per node. Keeps the audit, the preview wording, and the
  shelf refresh crisp.
- **Write contracts.** Principle: exactly
  `<<principles_dir>>/<slug>/{manifest.yaml, CONTEXT.md}` (contract
  carried over from `principle_init.md`, including the manifest shape
  and plan-free/self-contained/skimmable discipline). Skill:
  `<<skills_dir>>/<slug>/SKILL.md` plus supporting files; kebab-case
  slug; no symlinks; no writes outside the chosen target directory
  except the mandatory lane preview.
- **Refine-means-merge.** If the slug exists, read the current
  contents first and merge the requested improvement; never blow away
  hand edits or unrelated manifest fields.
- **Preview wording.** `summary` says "created principle `<slug>`" /
  "refined skill `<slug>`"; `next_implications` names the entry so
  downstream nodes and the shelves can react. Content is not pasted
  into the preview.

### 3.3 Runner validation and audit

Before the provider turn, the runner snapshots per-slug content
hashes for both libraries (`skill_content_hash` is generic enough for
both trees). After the turn reaches DONE — before preview repairs, so
an invalid library write fails the node regardless of preview state —
it diffs to find touched slugs and validates each:

- **Skill slugs:** `_validate_tree` (symlink rejection) +
  `inspect_agent_skill` (frontmatter, readability).
- **Principle slugs:** manifest parses as YAML with `kind: principle`
  and an `id` matching the directory; `CONTEXT.md` exists non-empty.
- **Cardinality:** exactly one touched entry. Zero → error ("librarian
  node finished without authoring anything"); more than one, or
  touches in both libraries → error naming the extras. (Interrupt and
  gate-cancel paths land in `cancelled` as usual; validation only
  runs on the DONE path.)

Any failure transitions the node to `error` with a message naming the
slug and reason, and writes the standard stub preview. Results —
touched slug, kind, created/refined, content hash, verdict — are
recorded in `settings_snapshot.library_audit`, symmetric with
`skill_audit`.

**Provenance:** a skill *created* by the librarian gets a
`skill-imports.json` entry `{import_kind: "authored", import_source:
"node:<node-id>", imported_at}`. Refinement leaves existing
provenance untouched (the field records how the files got there, not
who last wrote them).

Accepted risk: a librarian node and a concurrent manual edit (or a
librarian in another project) racing on the same slug — same exposure
manual edits already have with metadata sync; per-project serial
execution covers the common case.

### 3.4 Wire and API

No new endpoints, no event changes. `user_message` and virtual
create/promote already pass `agent_op_kind` through generically;
the domain whitelist is the only gate. `NodeStarted` already carries
the field. `GET /principles`, `GET /skills`, and the delete/import
endpoints are unchanged.

### 3.5 Frontend

- `App.tsx`: `handleNewLibraryEntry` mirroring `handleNewPrinciple`,
  creating a regular virtual with `agent_op_kind: "library_edit"` in
  the active planspace.
- `ProjectPanel.tsx`: the `+ New principle` seed control becomes
  **`+ New principle / skill`** (user vocabulary, not internal
  vocabulary) wired to the librarian; a principle seed goes through it
  identically. `Import skill` stays as-is for community skills.
- Shelf refresh: the terminal-state effect that today counts
  `principle_edit` nodes extends to `library_edit`, refreshing
  **both** shelves (the librarian may have written either kind).
- Node tile / `AgentPanel` badge the node "librarian".

### 3.6 Docs

On landing: `IMPLEMENTATION_STATUS.md` gains a "Librarian agents
(`library_edit`) — landed" section beside "Principle-edit agents",
removes the skill-authoring-concierge line from the v1 deferrals,
and the README's Principles/Agent Skills bullet mentions authoring.


## 4. Touch points

| Area | File(s) |
|---|---|
| Whitelist | `backend/miniclaw2/domain.py` |
| Prompt | `backend/miniclaw2/prompts/library_init.md` (new) |
| Launch block | `backend/miniclaw2/launch_prompt.py` |
| Dispatch, validation, audit | `backend/miniclaw2/runner.py` |
| Provenance helper | `backend/miniclaw2/skills.py` |
| UI entry, shelf refresh | `frontend/src/App.tsx`, `frontend/src/panel/ProjectPanel.tsx` |
| Badging | `frontend/src/canvas/nodes/`, `frontend/src/panel/AgentPanel.tsx` |
| Docs | `IMPLEMENTATION_STATUS.md`, `README.md` |

No changes: `sync.py`, `events.py`, `app.py` routes, store schema.


## 5. Testing

- `test_library_edit_prompt.py`, mirroring
  `test_principle_edit_prompt.py`: block composed only for
  `library_edit`, both directories substituted, first-slot ordering
  preserved.
- Runner validation: authored valid skill → DONE with `library_audit`
  and provenance; malformed `SKILL.md` → error naming slug/reason;
  symlink → error; zero touched slugs → error; two touched slugs →
  error; authored valid principle → DONE; refine leaves provenance
  untouched.
- Domain: whitelist accepts `library_edit`, still rejects unknowns;
  `agent_op_kind` remains agent-only.
- Frontend: shelf-refresh effect fires for both shelves on
  `library_edit` terminal states.


## 6. Out of scope

- Extraction mode ("distill this finished lane into a
  principle/skill") — natural follow-up; the unified kind and audit
  are designed not to preclude it.
- Import-time or authoring-time safety *scanning* of skill scripts —
  the trust story remains inspectability + content-hash audit, as
  with imports.
- Structured-form principle editing and the in-app manifest/markdown
  editor — separate pending item, unchanged.
- Binding-level "always available" skills, marketplace browsing,
  skill versioning — unchanged v1 deferrals.
