# Metadata Sync — Design (2026-07)

Cross-device/server synchronization of the MiniClaw2 metadata store
over a plain git remote. This document records the decided design; the
open questions raised during design are resolved inline and the
deliberately deferred pieces are listed in §9.

The decisions in one paragraph: `$MINICLAW_HOME` itself becomes a git
repository synced to a user-provided remote. Every project is **native
to exactly one machine** — the machine that created it — and is
**read-only everywhere else**, which makes every `projects/<pid>`
subtree single-writer by construction and eliminates leases, path
mapping, and merge drivers for project data. Machine identity lives in
one gitignored `machine.json`. The only multi-writer surfaces are the
global files (`config.json`, `contextspace/`), resolved with
`merge -X ours` (local wins on conflicting hunks; history keeps the
loser). Sync is manual-only: a "Sync now" button in the Global
Settings panel next to an up-to-date/changed status; MiniClaw2 never
touches the network on its own. Artifacts do not sync. Ownership
transfer ("adopt project") is deferred.


## 1. Premise: the durable state is already centralized

There are two `.miniclaw2` folders with very different roles:

- `$MINICLAW_HOME` (default `~/.miniclaw2`) — the **source of truth**:
  `config.json`, `schema.json`, `projects/<pid>/{project.json,
  nodes/<nid>/{node.json, events.jsonl, gates.jsonl, preview.json}}`,
  and `contextspace/{bindings, plugs, snapshots, templates}`.
- `<project_root>/.miniclaw2` — a **regenerable projection**: lane
  materializations rebuilt from the store before every launch
  (`materialize.py`), per-run scratch, and `outputs/<nid>/` artifacts.
  It is force-excluded from the project's own git via
  `.git/info/exclude` (`git_state.py`).

So the sync target is `$MINICLAW_HOME`, and the project-dir folder is
untouched by this design. This is worth stating because it means sync
does not have to fight "metadata scattered across N checkouts" — the
centralization the store already has is exactly what sync wants.

What blocks sync today is not layout but content:

1. Absolute paths baked into synced records — `project.root_path`
   (`registry.py`, resolved at creation) and binding `local_paths`
   (`contextspace.py`).
2. Machine-bound provider state — `node.provider_session_id` /
   `provider_turn_id` reference a Claude PTY session (transcript in
   `~/.claude/`, outside the store) or a Codex app-server thread;
   neither can resume on another machine.
3. No multi-writer story — `node.json` is rewritten per transition,
   `project.json` absorbs high-frequency layout writes, and the only
   locks are in-process.

The native-machine convention (§2) dissolves all three: paths are only
ever interpreted where they are valid, provider sessions are only ever
resumed where they exist, and project subtrees have one writer.


## 2. The core convention: native-machine ownership

**A project is native to the machine that created it. On every other
machine it is read-only, always.**

Consequences, in order of importance:

- **Single writer per project subtree.** All writes under
  `projects/<pid>/` — `node.json` rewrites, `events.jsonl` appends,
  gate records, previews — happen on one machine. Git merges across
  machines touch disjoint paths and never conflict on project data. No
  union-merge `.gitattributes`, no LWW timestamps, no custom merge
  drivers for JSON.
- **No path mapping.** `root_path` stays absolute in `project.json`
  and binding `local_paths` stay as they are; they are only meaningful
  on the native machine, and the native machine is the only place that
  dereferences them. A non-native machine never needs to know where
  the project lives.
- **No leases.** Ownership is static, not session-scoped. There is
  nothing to acquire, refresh, or expire, and no takeover race.
- **Sync means visibility, not portability of execution.** Other
  devices get the graph, transcripts, previews, and history — a
  read-only window. Running nodes, resuming sessions, editing the
  plan: native machine only.

The trade accepted here: a retired or dead machine leaves its projects
permanently read-only. The escape hatch — an explicit, audited "adopt
project" transfer — is deferred (§9).


## 3. Machine identity: `machine.json`

"Read-only if not native" requires each machine to know whether it
*is* the native machine. Two zero-state tests were considered and
rejected:

- *`root_path` exists on disk* — false positives are easy
  (`/Users/alice/proj` on two Macs with the same username), and a
  false positive silently creates two writers, collapsing the entire
  concurrency model.
- *Hostname* — changes on rename, collides in containers and VMs.

Instead: one gitignored file, `$MINICLAW_HOME/machine.json`, holding a
uuid generated on first run plus the hostname as a human display
label. `project.json` gains a synced `machine_id` field; nativeness is
`project.machine_id == machine.json id`. This is not a `local/`
directory split — it is a single ignored file.

The recorded hostname doubles as a copy detector: if it no longer
matches the actual hostname at startup, MiniClaw2 asks once — "renamed
machine, or copied store?" — because wholesale-copying `$MINICLAW_HOME`
(instead of cloning the remote) would duplicate the identity and
reintroduce two writers. "Copied" regenerates the uuid, which
correctly demotes every project native to the original machine to
read-only on the copy.

Node records additionally benefit for free: a `provider_session_id` is
resumable only where it was created, and `machine_id` gives the UI an
honest reason to disable "resume from node" instead of letting a
cross-machine resume fail confusingly. (Under strict read-only
enforcement a non-native resume is already impossible; the point is
the *explanation* the UI can now give.)


## 4. Repo shape

`$MINICLAW_HOME` is itself the git repository, synced to a plain
user-provided remote (GitHub private repo, Gitea, bare repo over SSH —
anything `git push` reaches). No `store/`/`local/` split; the things
that must not sync are few enough for a small in-repo `.gitignore`:

```
machine.json
migration-backups/
*.tmp
```

Everything else syncs: `schema.json`, `config.json`, `projects/`
(including `events.jsonl` transcripts, gate ledgers, previews), and
`contextspace/` (bindings, plugs, skills, templates, snapshots).

Two adjustments to existing conventions:

- `contextspace.yaml` currently declares `git: {expected: true}` — the
  notion that the ContextSpace root is its own git repo. Dropped: a
  nested repo inside the store repo is a submodule mess, and the store
  repo now provides the versioning the flag was reserving. This also
  delivers the long-deferred "automatic ContextSpace git commits"
  (README, out-of-scope list) as a side effect of ordinary sync
  commits.
- `outputs/<nid>/` artifacts are **excluded** from sync. They live in
  the project dir (not in `$MINICLAW_HOME`), can be large and binary,
  and their value is local to the machine that can run the project
  anyway.

Transcript volume is accepted for v1: `events.jsonl` is append-only
text, which git packs well, but the repo grows monotonically. The
retention story (archiving old projects to a prunable prefix,
compaction, LFS) is deferred until it hurts (§9).

Privacy is a setup-time disclosure, not a mechanism: the store
contains **full transcripts of every agent run** — prompts, tool
output, code. The sync-setup UI says so plainly and recommends a
private remote. Encryption at rest (git-crypt/age) is a plausible
follow-up, not v1.


## 5. Conflict policy

Project subtrees are single-writer (§2), so conflicts can only arise
on the files legitimately writable from any machine:

- `config.json` — global defaults and the model preset catalog;
- `contextspace/plugs/**` — skill-edit nodes run wherever their
  project is native, so any machine can write skills;
- `contextspace/templates/**` — same reasoning for user templates.

Disjoint edits merge cleanly. For the rare same-hunk conflict the rule
must never block sync and never ask a user to hand-edit JSON:

**`git merge -X ours`** — local wins on conflicting hunks only. The
losing version remains recoverable in git history, and both machines
converge after the next push. Local-wins is correct because the local
edit is the user's most recent expressed intent on the machine they
are actually sitting at.

One deliberate exception: a conflict on `schema.json` is a **hard
failure** requiring manual resolution. It means two machines ran a
store migration independently while offline — nearly impossible to
hit, and genuinely dangerous to auto-resolve, since `-X ours` could
splice a half-migrated store. The startup ordering rule (§7) makes
this configuration unreachable in normal operation.


## 6. Read-only semantics on non-native machines

- **Backend-enforced, not just UI.** Every mutating REST/WS operation
  on a non-native project — `user_message`, node rerun, virtual
  create/edit/promote, settings PATCH, project DELETE, context
  init/refresh — is rejected with a clear error naming the native
  machine. The UI hiding buttons is a courtesy; the API guard is the
  actual invariant, because a false write is what would break the
  single-writer model.
- **Honest staleness.** A viewer sees the last synced checkpoint, not
  a live stream. A node can appear `running` on machine B long after
  machine A finished or crashed. The project surface carries a badge:
  *read-only · native to alice-mbp · as of last sync 14:32*. The
  timestamp is what keeps a stale `running` state honest.
  Cross-machine live streaming (backend-to-backend event relay) is a
  different feature and out of scope.
- **Layout is session-only when read-only.** Pan/zoom/drag in a
  read-only canvas is never persisted. This also disposes of the
  layout-churn concern (frequent `layout_hints` writes) without
  splitting layout into its own file: on the native machine layout
  writes ride the existing debounced commit cadence; on viewers they
  do not exist.
- **Creation is always local.** A new project created on any machine
  is native to it; new `projects/<pid>` subtrees merge as pure
  additions. Deletion is a native-machine action that propagates as an
  ordinary subtree removal on pull.


## 7. Sync mechanics

**Commit** at durable boundaries the store already has, debounced
(~30s coalescing):

- node terminal transition, after reap;
- project create / settings edit / delete;
- ContextSpace plug, skill, and template edits;
- `config.json` changes.

Commit messages are structured (`node a1b2c3 done in "MiniClaw2"`) and
the commit author carries the machine label, so `git log` doubles as a
cross-machine activity ledger.

**Sync** = `fetch` + `merge -X ours` (§5) + `push`, and it is
**manual-only**: the single trigger is the "Sync now" button in the
Global Settings panel. MiniClaw2 never touches the network on its
own — no sync on start or stop, no periodic pull. Commits (above) are
purely local operations and keep accumulating regardless; the button
is what exchanges them with the remote. A failed or offline sync
leaves the store exactly as it was — commits keep queuing and the
status stays "changed". Sync never surfaces a git prompt; the one
hard failure is the `schema.json` case in §5.

**Sync status** is shown next to the button, as a binary:
**up-to-date** (working tree clean, nothing local beyond the last
successful sync) or **changed** (anything committed or pending that
the remote has not seen). The status is computed locally — manual-only
sync means a machine cannot know about remote-side changes without
being asked, so remote divergence is discovered (and merged) at
button-press time, and viewer freshness on non-native projects is
exactly as fresh as the last button press; §6's "as of last sync"
badge is what keeps that honest.

**Schema migrations** run at startup as today when the code is newer
than the store. With manual-only sync the coordination becomes
documented practice rather than an automatic ordering: press Sync
before upgrading MiniClaw2, and again after the migration runs — the
migration commit reaches the remote on that second press. The
existing migration machinery (versioned `schema.json`, journaled
migrations, `migration-backups/`) is reused as-is; backups stay local
(gitignored). A machine whose code is older than the store's
`schema_version` opens the store read-only rather than writing.
Upgrade one machine at a time; the `schema.json` hard failure (§5) is
the backstop when that practice is violated.

**Bootstrap**, two flows:

- *Fresh machine* — `miniclaw2 sync init <git-url>` clones the remote
  into `$MINICLAW_HOME`, generates `machine.json`; every synced
  project appears read-only, and the machine starts creating its own.
- *Existing store, empty remote* — `git init` in place, initial
  commit, push.
- *Both non-empty* — refused in v1. Merging two independently grown
  stores is a real problem with no urgent user.


## 8. Implementation sketch

- `domain.py` — `Project.machine_id: str`; store schema bump
  (canonical-schema-v4) stamping existing projects with the local
  machine id (they are, by definition, native here).
- new `sync.py` — machine identity (create/read `machine.json`,
  hostname mismatch prompt), repo init/clone, debounced commit queue,
  manual fetch/merge/push with the §5 policy, and the locally
  computed up-to-date/changed status for the UI.
- `registry.py` / `app.py` — nativeness check on project load; guard
  every mutating endpoint and WS message for non-native projects;
  wire commit triggers into the reap/settings/skill paths.
- `global_config.py` / Global Settings UI — remote URL configuration,
  the "Sync now" button, the up-to-date/changed status plus last-sync
  time; setup-time privacy disclosure.
- frontend — read-only badge (`native to <label> · as of <time>`),
  hidden direction controls and virtual-node actions on non-native
  projects, disabled resume where `machine_id` differs.


## 9. Deferred

- **Adopt-project transfer.** The explicit ownership-transfer action
  (rewrite `machine_id` + `root_path`, invalidate stale provider
  sessions). Until built, a dead machine's projects remain read-only
  everywhere. Deliberately deferred; the design hole is known.
- **Retention/compaction.** Archive prefix for old projects, transcript
  compaction, LFS for anything large. Revisit when repo size hurts.
- **Encryption at rest** (git-crypt/age) for users whose remote trust
  is partial.
- **Artifact sync.** `outputs/` stays local; a size-capped opt-in
  could come later.
- **Cross-machine live streaming.** Viewer freshness beyond
  pull-on-sync would need backend-to-backend relay; different feature.
- **Multi-master.** Two concurrently *writing* machines on one store
  is explicitly out of scope; the native-machine convention is the
  concurrency model.
- **Merging two grown stores** at bootstrap time (§7, "both
  non-empty").
