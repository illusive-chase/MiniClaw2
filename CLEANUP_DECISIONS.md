# Dead-code and compatibility cleanup decisions

Status: accepted for the schema v3 cleanup implemented on 2026-07-10.

This record resolves the internal decisions required by
`DEAD_CODE_COMPAT_CLEANUP_PLAN.md`. Decisions that require information about
external users or supported vendor versions remain explicitly conservative.

## ADR-001: Store migration lifecycle

- `python -m miniclaw2` runs the schema migration before Uvicorn starts.
- Constructing `Store` never migrates or scans the whole store.
- A current schema version takes the cheap startup path. Full consistency
  validation is explicit through `--check-store`; `--repair-store` repairs a
  current-version store containing legacy records.
- `--dry-run-migration` migrates a temporary copy and reports planned files,
  backup root, and audit path without modifying the source.
- Migrations use per-file backup, rollback, audit records, legacy-shape counts,
  and write the schema marker only after validation succeeds.

## ADR-002: Provider persistence

- Project and Node JSON persist only `model_preset_id`.
- `Project.provider` and `Node.provider` are computed properties used by wire
  responses and runtime display.
- Provider adapter selection derives from the model preset catalog.
- Historical provider/model fields are accepted only by schema migration.

## ADR-003: ContextSpace selection ownership

- `Project.project_context_binding_id` is the explicit binding owner.
- `Project.active_planspace_id` is the active planspace owner.
- `Project.preferred_language` is the language owner.
- Schema migration absorbs legacy settings keys, rejects conflicting binding or
  planspace selections, removes binding-manifest active state, and persists a
  sole discoverable planspace when unambiguous.
- Root-path matching remains a discovery mechanism for projects without an
  explicit binding. Creating or migrating a binding persists the typed id.

## ADR-004: Interaction response schema

- Ask-user answers use
  `response.answers.<question-id>.answers: string[]`.
- Human-review prose uses `response.prose`.
- Permission UI responses use provider-neutral `allow`, `scope`, `interrupt`,
  `message`, and `updated_input`; provider adapters generate vendor decisions.
- Historical response carriers are normalized only by the replay/deserialization
  upgrader used for legacy fixtures.

## ADR-005: HTTP API stability

- `/sessions` remains the canonical HTTP namespace for this cleanup because the
  repository does not contain evidence that external HTTP users can accept an
  immediate rename, and no removal release has been selected.
- `/projects` is not introduced as a duplicate implementation. A future rename
  must use shared handlers, a deprecation header, and a documented removal
  version.
- The planspace request field is `seed`. `user_seed` remains a thin deprecated
  input alias and returns `Deprecation` and `Warning` headers.

## ADR-006: Codex approval compatibility

- Both current and legacy Codex approval RPC methods remain supported because a
  minimum supported Codex CLI/app-server version and capability probe are not
  yet defined.
- The frontend submits provider-neutral permission intent. The adapter owns both
  current and legacy decision vocabularies.
- Removing legacy RPC handlers requires a minimum-version policy and two-version
  compatibility coverage first.

## ADR-007: Event replay versioning

- New `events.jsonl` records carry `schema_version: 2`.
- Replay upgrades version-1 `checkpoint_review` interaction requests to current
  `human_review_prose` before runtime or UI delivery.
- The current backend and frontend event unions do not contain
  `checkpoint_review`.

## Deferred public compatibility choices

- `SessionInfo.provider` remains as a response-only derived field because its
  removal is an HTTP compatibility decision; it is not persisted.
- The `/sessions` namespace and legacy Codex approval methods remain until the
  external-user and minimum-version questions above have concrete answers.
