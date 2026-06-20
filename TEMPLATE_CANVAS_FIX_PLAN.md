# Template Canvas Fix Plan

This document captures four related issues observed while running bundled
template test cases. It is intended as an implementation handoff for another
agent. The current diagnosis is based on reading the existing frontend and
backend code; no product behavior has been changed yet.

## Scope

The affected surface is the template-backed temporary project canvas:

- bundled templates launched from `POST /templates/{name}/run`
- pre-created virtual lanes inside those projects
- React Flow node/edge materialization
- layout persistence and reload behavior
- node refresh after a run completes

Relevant files:

- `backend/miniclaw2/templates/launcher.py`
- `backend/miniclaw2/registry.py`
- `backend/miniclaw2/app.py`
- `frontend/src/App.tsx`
- `frontend/src/canvas/Canvas.tsx`
- `frontend/src/canvas/layout.ts`
- `frontend/src/canvas/edges/TimelineEdge.tsx`
- `frontend/src/canvas/nodes/AgentNode.tsx`
- `frontend/src/panel/AgentPanel.tsx`

## Verified Baseline

The following existing tests passed while preparing this handoff:

```bash
python -m pytest backend/tests/test_layout_state_api.py backend/tests/test_templates_launch.py -q
```

Result:

```text
6 passed
```

This matters because backend layout persistence and template instantiation are
not completely broken at the storage/API layer. Some observed behavior is
frontend state or missing UI/API capability.

## Issue 1: Users cannot add virtual nodes into a predefined template lane

### Observed Behavior

After launching a template test case, the canvas contains predefined virtual
nodes. The user cannot add a new virtual node into that lane before running the
existing nodes. It may feel like the lane is locked until the predefined nodes
complete.

### Current Code Behavior

Template launch stamps the whole template lane into a new temporary project:

- `backend/miniclaw2/templates/launcher.py`
  - `launch_template(...)` creates a temporary project and planspace.
  - `_instantiate_lane(...)` creates all template nodes as `NodeState.VIRTUAL`.
  - The template DAG is stored in `scheduled_deps`.

There is no generic user-facing API to create a new virtual node:

- `POST /sessions/{sid}/virtuals/{vid}/promote` promotes an existing virtual.
- `PATCH /sessions/{sid}/virtuals/{vid}` edits an existing agent virtual.
- There is no `POST /sessions/{sid}/virtuals` endpoint.

The canvas `+` button does not create a virtual node. It opens the phantom
composer, and submitting the composer sends a websocket `user_message`, which
creates a queued/running agent via `registry.start_node(...)`.

`AgentPanel` can edit existing agent virtuals, but programmatic verifier
virtuals are read-only and rendered through `VerifierVirtualBody`.

### Root Cause

The product vocabulary currently conflates two operations:

- "follow-up run" from a completed node, which creates an executed run
- "create planned virtual node", which does not exist as a direct user action

Template lanes are pre-created DAGs, but there is no manual insertion path for
new user-authored virtuals.

### Desired Behavior

Decide explicitly whether user-authored virtuals are supported in template
projects.

If supported:

- Users should be able to add a virtual node to the active planspace lane while
  no node is running.
- The new virtual should have `prompt_draft`, `category`, optional review
  fields, and `scheduled_deps`.
- The new virtual should appear immediately on the canvas.
- It should be editable with the existing virtual panel.
- It should participate in auto/manual promotion exactly like template-created
  virtuals.

If not supported:

- The UI should avoid implying that the `+` or phantom composer creates virtual
  nodes.
- Template lanes should make clear that predefined virtuals can be edited or
  promoted, but new planned nodes are not available.

### Recommended Implementation

Implement user-authored virtual creation.

Backend:

- Add `ProjectRegistry.create_virtual(...)` or equivalent.
- Add `POST /sessions/{sid}/virtuals`.
- Validate:
  - project exists
  - no node is currently running
  - context refresh is not running
  - target lane is active or explicitly provided and belongs to the project
  - `prompt_draft` is non-empty
  - category/review subtype/brief invariants match `Node`
  - every `scheduled_dep` resolves
  - every `scheduled_dep` is in the same lane
  - no self-dependency
  - no cycle in the lane DAG
- Persist the virtual node and write `render_virtual_preview(node)`.
- Broadcast `node_updated` or a new `node_created` event. Reusing
  `node_updated` is probably enough because the frontend upserts nodes.

Frontend:

- Add an API helper in `frontend/src/api.ts`, for example `createVirtual(...)`.
- Add UI affordance from the active lane or phantom composer:
  - lane header "Add virtual" is clearer than overloading `+`
  - optionally allow PhantomNode to submit as "Save draft" instead of "Launch"
- Reuse the existing `VirtualNodeBody` editing UI after creation.

Tests:

- Backend API test for creating a virtual in a template project.
- Validation tests for cross-lane deps, missing deps, self-deps, and cycles.
- Frontend smoke/unit coverage if the project has existing test setup for UI
  state handlers.

## Issue 2: Same template project does not restore saved layout after reopening

### Observed Behavior

The user confirmed this is not about launching a new template run. The failing
case is:

1. Launch a template test project.
2. Drag/reposition nodes, lane, or viewport.
3. Return to the Projects screen.
4. Reopen the same test project id.
5. The layout is not restored.

Expected behavior: reopening the same project id should restore the saved
canvas layout, just like a normal project.

### Current Code Behavior

Backend layout persistence is project-level and does not distinguish normal,
temporary, or template projects:

- `Project.layout_hints`
- `Project.layout_viewport`
- `PATCH /sessions/{sid}/layout-hints`
- `ProjectRegistry.update_layout_hints(...)`
- `_session_info(...)` returns the saved fields

The existing layout API test verifies round-trip persistence across app
recreation.

Important non-goal:

- Running the same template again creates a brand-new temporary project with new
  node ids. It does not need to inherit layout from previous template runs for
  this fix. That would be a separate per-template/default-layout feature.

### Likely Frontend Cause

`Canvas` stores `initialLayoutHints` in a ref:

- `frontend/src/canvas/Canvas.tsx`
  - `layoutHintsRef.current = sanitizeLayoutHints(initialLayoutHints)`
  - `buildGraph(...)` reads `layoutHintsRef.current`

But the `useMemo` that calls `buildGraph(...)` does not include
`initialLayoutHints` or a layout revision in its dependency list. Also, the
upstream-node sync effect preserves current React Flow positions by id:

```ts
const positionById = new Map(current.map((n) => [n.id, n.position]));
...
if (existing && (existing.x !== n.position.x || existing.y !== n.position.y)) {
  return { ...n, position: existing };
}
```

That behavior is useful during drag and live node updates, but it can mask newly
hydrated saved positions after project/session state changes.

Because the failing case is reopening the same project id, the implementation
should focus on `Canvas` remount/hydration and `App.openProject(...)` state,
not on backend storage. Backend persistence already round-trips in
`test_layout_state_api.py`.

### Desired Behavior

- Dragged node and lane positions persist when reopening the same project.
- Saved viewport persists when reopening the same project.
- Live node updates should not reset user drag positions.
- Explicit project/session hydration should honor backend `layout_hints`.
- Running a new template should continue to start with default layout unless a
  separate per-template layout feature is introduced later.
- Do not accidentally make previous temporary template runs share layout.

### Recommended Implementation

Frontend:

- Replace the layout-hints ref-only hydration with a layout revision/state.
- Include that revision in the `buildGraph(...)` memo dependencies.
- On `initialLayoutHints` change for the current mounted `Canvas`, perform a
  controlled hydration pass where backend hints win over current React Flow
  positions.
- Keep the current-position preservation only for live node updates or active
  drag state.

Possible approach:

- Track a `layoutHydrationVersion` that increments when
  `initialLayoutHints` changes.
- In the `setRfNodes` sync effect, if the version changed, do not use
  `positionById` to override built positions for that pass.
- After hydration, return to the normal "preserve current positions" behavior.

Diagnostics:

- Add temporary console diagnostics around `onLayoutHintsChange` and
  `/layout-hints` failures while validating.
- Verify the same project id is used before and after returning from Projects.
- Verify the reopened `GET /sessions/{sid}` response contains non-empty
  `layout_hints` and/or `layout_viewport`.
- If the response contains saved layout but the canvas renders defaults, the
  bug is definitely in frontend hydration/sync.
- If the response is empty, inspect whether the save request was skipped,
  failed, or not flushed before navigating back to Projects.

Tests:

- Extend layout API tests to cover `temporary=True` if desired.
- Add a focused frontend test for `Canvas` hydration if a React test harness is
  available.
- Manual validation:
  1. Launch template.
  2. Drag a node and the lane.
  3. Return to project list.
  4. Reopen the same project id.
  5. Confirm node/lane/viewport positions remain.
  6. Confirm launching a new run of the same template still starts with default
     layout unless a separate default-layout feature was implemented.

## Issue 3: Canvas arrows come from root/home; `scheduled_deps` are never drawn

### Observed Behavior

All visible arrows in a template lane appear to originate from the project root
or "home". The dependency relationships from the template DAG do not appear on
the canvas.

### Current Code Behavior

Template dependencies are stored in `scheduled_deps`:

- `_instantiate_lane(...)` maps template slugs to node ids.
- `node.scheduled_deps = [...]`.

Frontend graph materialization does not create edges from `scheduled_deps`.
`buildGraph(...)` only creates primary edges from `parent_node_id`, op folding,
or root fallback:

- op-chevron edge
- `parent_node_id -> node`
- `root -> node` when `parent_node_id` is absent

Because template virtual nodes usually have no `parent_node_id`, they fall into
the root-edge path.

The frontend does read `scheduled_deps` only for readiness display:

- `isVirtualReady(...)`
- virtual tile footer displays `ready` or `N deps`

### Root Cause

The backend model has two different graph relationships:

- `parent_node_id`: execution/resume/timeline lineage
- `scheduled_deps`: planning/template DAG dependencies

The canvas currently renders only the first relationship as graph edges.

### Desired Behavior

- `scheduled_deps` should be visible as dependency edges.
- Template lanes should show their DAG structure before any node runs.
- Dependency edges should not be confused with resume edges or timeline edges.
- Root edges should remain only for genuinely root-started nodes, not as a
  substitute for dependency edges.

### Recommended Implementation

Frontend:

- Add a dependency edge type in `TimelineEdge.tsx`, for example
  `DependencyEdge`.
- Register it in `EDGE_TYPES` in `Canvas.tsx`.
- In `buildGraph(...)`, after visible nodes are known:
  - for each node
  - for each `depId` in `node.scheduled_deps`
  - if dep is visible, add `depId -> node.id` edge of type `dependency`
- Suggested edge style:
  - dashed
  - low opacity
  - maybe no animated state
  - marker end enabled
  - selected/hovered endpoint can increase opacity
- Avoid duplicate visual clutter:
  - if the same pair is already represented by a resume/review/timeline edge,
    either skip the dependency edge or mark the main edge with dependency data
  - if there are multiple dependency edges to one node, keep them all because
    the DAG fan-in matters
- Consider whether root fallback should be suppressed for nodes that have
  visible `scheduled_deps`. A node with dependencies probably should not also
  show a root edge just because `parent_node_id` is null.

Also update `isLastInLane`:

- It currently uses only `parent_node_id` descendants.
- For a DAG lane, a node with downstream `scheduled_deps` children is not
  really a lane tail.
- Include `scheduled_deps` when computing descendants/tail status.

Tests:

- Add a unit test for `buildGraph(...)` if no test exists:
  - input: three nodes A, B, C, with B deps [A], C deps [A, B]
  - expected: dependency edges A->B, A->C, B->C
  - expected: no root edge for B/C if the implementation suppresses root
    fallback when dependency edges exist
- Manual validation with `hello-text`:
  - first node has no deps
  - verifier depends on first
  - accept depends on first and verifier
  - all dependency edges should be visible before promotion

## Issue 4: Canvas clears when a test node finishes

### Observed Behavior

When a test/template node finishes, the canvas is cleared immediately. Reopening
the project restores the graph.

### Current Code Behavior

There is no obvious backend path that deletes nodes on completion. Reopening the
project restoring the graph strongly suggests persistence is intact and the
problem is a frontend state refresh issue.

Frontend risk area:

- `handleEvent(...)` handles `turn_done` by calling `setStreaming(false)` and
  `refreshNodes()`.
- `refreshNodes()` calls `listNodes(session.id)` and then unconditionally
  `setNodes(next)`.
- No request token or captured-session guard prevents stale refresh responses
  from replacing the current project's nodes.
- If any refresh returns `[]` or belongs to stale session state, the canvas
  becomes empty.

Relevant files:

- `frontend/src/App.tsx`
  - `refreshNodes`
  - `handleEvent`
  - websocket event handling
  - empty canvas CTA gated by `nodes.length === 0`
- `frontend/src/ws.ts`
  - reconnect/replay behavior

### Desired Behavior

- Completion should reconcile node state without clearing the canvas.
- A stale or out-of-order refresh must not replace the current project state.
- A temporary transient empty response should be logged and guarded against
  once a project already has known nodes.
- Reopening should not be necessary to recover from a normal completion event.

### Recommended Implementation

Frontend:

- Add request/session guarding to `refreshNodes`.
- Capture `session.id` at request start.
- On response, before `setNodes(next)`:
  - confirm current session id still matches the captured id
  - confirm route is still `project`
  - optionally confirm the response is not suspiciously empty for a project
    that already had nodes

Example shape:

```ts
const refreshNodesSeqRef = useRef(0);

const refreshNodes = useCallback(async () => {
  const sessionId = session?.id;
  if (!sessionId) return;
  const seq = ++refreshNodesSeqRef.current;
  try {
    const next = await listNodes(sessionId);
    if (seq !== refreshNodesSeqRef.current) return;
    setNodes((current) => {
      // If this project already had nodes and the backend unexpectedly returned
      // none, keep current nodes and log. Tighten or remove this guard after the
      // root cause is confirmed.
      if (current.length > 0 && next.length === 0) {
        console.warn("Ignoring empty node refresh for non-empty project", sessionId);
        return current;
      }
      return next;
    });
    setInspectedNodeId((current) => current ?? next.at(-1)?.id ?? null);
  } catch (err) {
    console.error("list nodes failed:", err);
  }
}, [session?.id]);
```

The exact implementation should avoid stale closures. A `currentSessionIdRef`
may be cleaner than reading `session` inside async continuations.

Also consider:

- Treat `node_updated(done)` as the primary state update and `turn_done`
  refresh as a reconciliation pass.
- Add logging around `/sessions/{sid}/nodes` responses during the failing case:
  - session id
  - number of nodes returned
  - current nodes length
  - event type that triggered the refresh
- If network shows `/sessions/{sid}/nodes` returning a non-empty list while the
  canvas is empty, inspect `buildGraph(...)` filtering:
  - hidden planspace state
  - `knownPlanspaceIds`
  - active planspace contextspace refresh

Tests:

- Add a frontend state test for stale refresh responses if the project has a
  React/App test harness.
- Manual validation:
  1. Launch `hello-text`.
  2. Promote/run first node.
  3. Wait for completion.
  4. Confirm canvas still shows all template nodes and updated state.
  5. Repeat with a verifier and accept node.

## Suggested Implementation Order

1. Add dependency edge rendering for `scheduled_deps`.
   This is the most deterministic bug and will make template lanes readable.

2. Add guarded node refresh in `App.tsx`.
   This should address the high-impact canvas clearing behavior or at least
   produce diagnostic logs that isolate it.

3. Clarify and implement user-authored virtual creation.
   Add the missing backend endpoint and a clear UI affordance if the product
   should support this.

4. Fix layout hydration for reopening the same template project id.
   Update `Canvas` hydration so backend hints win on project/session hydration
   but live updates still preserve drag positions.

## Acceptance Checklist

- A freshly launched template lane displays dependency edges from
  `scheduled_deps`.
- Non-dependent template nodes do not all appear as root-driven timeline
  children unless they genuinely have no dependencies.
- The first completed template node does not clear the canvas.
- Reopening the same template project preserves saved node/lane positions and
  viewport.
- Running a new template project has clearly defined layout behavior.
- If user-authored virtual creation is implemented, users can add an agent
  virtual to the active lane before all predefined template nodes have run.
- Programmatic verifier virtuals remain template-authored only unless the
  product explicitly decides otherwise.
- Existing backend tests continue to pass.
