# Rendered Artifacts — Design (2026-07)

User-facing display of agent-produced artifacts on the graph canvas.
This document records the decided design; the open questions raised
during design are resolved inline and the deliberately deferred pieces
are listed in §9.

The decisions in one paragraph: an agent **publishes** an artifact by
writing a file under its own `.miniclaw2/outputs/<node-id>/` directory
and **declaring the filename in its preview** (`"artifacts":
["report.md"]`). Only three suffixes render: `.md`, `.json`, `.html`.
At the terminal reap the framework validates each declared entry
(exists, allowed suffix, within size caps), copies the published files
into the metadata store at `projects/<pid>/nodes/<nid>/artifacts/` —
so they **sync** and are viewable on read-only machines — and stamps a
manifest onto the `Node` record, which rides the existing
`node_updated` event. The canvas renders each published artifact as a
`ContextNode`-style tile hanging below its producing agent, connected
by a `produces` edge (CONTEXT.md is itself a special global artifact;
the tiles follow its visual language). Clicking a `.md` or `.json`
tile shows the content inline in the side panel with the same
markdown/highlight treatment as the project `CONTEXT.md` viewer;
clicking an `.html` tile opens a new browser window served with
`Content-Security-Policy: sandbox allow-scripts` so agent-authored
HTML runs in an opaque origin with no access to the MiniClaw2 API.
Undeclared files stay on disk and keep flowing to downstream agents
through the lane projection, but are never rendered or synced.
Declared-but-invalid entries are dropped with a recorded reason, not
repaired. Artifacts appear only at terminal transition.


## 1. Premise: outputs exist but are invisible

`<project_root>/.miniclaw2/outputs/<nid>/` is already the designated
artifacts directory (`ARTIFACTS_DIRNAME`, `materialize.py:28`). Today
it is opaque end to end:

- Nothing in the launch contract tells the agent the directory
  exists or what to put there. The category prompts
  (`prompts/category_*.md`) describe the lane's `artifacts/` folder
  as something to *read*, never something to *produce into*.
- `materialize.py` copies the directory verbatim into the lane
  projection for downstream agents; no filtering, no size limits.
- `reap.py` ignores it entirely — reap only validates preview writes.
- No REST endpoint serves it. The only file the UI can fetch from a
  project workspace is `CONTEXT.md`
  (`GET /sessions/{sid}/files?role=context`, `app.py:675`).
- Nothing on the canvas or in any panel shows that a node produced
  files at all.

Meanwhile PHILOSOPHY §8 deliberately removed the former
`output_kind` / result-file ontology, and the landed metadata-sync
design (IMPLEMENTATION_STATUS §10) classifies
`<project_root>/.miniclaw2` as a regenerable projection that does not
sync ("Artifacts do not sync").

This design gives artifacts a user-facing surface without undoing
either decision. It does not reintroduce an output-kind enum — the
preview remains the node's single semantic output — and it does not
sync the workspace outputs directory. What it adds is a **publication
step**: a declared, validated, store-copied subset of outputs that the
GUI renders and sync carries. §8 discusses the philosophy alignment
in detail.


## 2. The publication contract: declared in the preview

The core question was how the framework decides which files render.
Three options were considered:

1. *Render everything with an allowed suffix* — the framework scans
   the outputs directory and renders whatever conforms. Rejected:
   agents produce scratch files, intermediate data, and
   agent-to-agent handoff files; rendering all of them turns the
   canvas into a file browser and forces a hard question about
   non-conforming files (delete? warn? fail?).
2. *Hard-constrain the directory* — reap deletes or rejects
   non-conforming files. Rejected: destroys agent work product and
   breaks the existing opaque agent-to-agent channel through the
   lane's `artifacts/` folder.
3. *Explicit declaration in the preview* — **chosen**. The preview is
   already the node's one required output and the one place the agent
   states what it did. Publishing is an intentional act: the agent
   lists the filenames it wants the human to see.

`ExecutedPreview` (`preview.py`) gains one optional field:

```json
{
  "id": "<node-id>",
  "kind": "agent",
  "category": "regular",
  "state": "done",
  "...": "...",
  "artifacts": ["report.md", "metrics.json", "demo.html"]
}
```

- Entries are bare filenames, resolved against the node's own
  `.miniclaw2/outputs/<node-id>/`. No path separators, no `..`.
- The field is optional and defaults to `[]`; existing previews and
  the framework-written stubs remain valid unchanged (the model is
  `extra="forbid"`, so this is a schema addition, but an additive
  one — old previews parse fine).
- `VirtualPreview` does not carry the field; virtuals have produced
  nothing.
- Any reaped preview may declare artifacts, so verifier nodes (whose
  scripts write normal previews) can publish reports too. Op nodes
  bypass the reap pipeline and never publish.

Files **not** declared are invisible: they stay on disk, they keep
being copied into the lane projection for downstream agents
(`materialize.py` is untouched), but they are never rendered, never
listed, and never synced. This resolves the non-conforming-file
question without enforcement: the whitelist constrains what can be
*published*, not what can be *written*.

### 2.1 The launch contract addition

Every agent category template gains a short section (token
`<<outputs_path>>` is a new substitution in `launch_prompt.py`
resolving to the absolute
`<project_root>/.miniclaw2/outputs/<node-id>/`):

```markdown
## Publishing artifacts (optional)

To show a file to the human, write it under:

    <<outputs_path>>

then list its filename in the `artifacts` field of your preview:

    "artifacts": ["report.md"]

Only declared files ending in `.md`, `.json`, or `.html` are shown.
An `.html` file must be a single self-contained document — inline
CSS and JS, no external assets, no companion files. Keep artifacts
few and final: they are a publication for the human, not a scratch
space. Files you do not declare remain readable by later agents but
are never shown to the human.
```

### 2.2 Validation at reap — drop, don't repair

At the terminal reap, each declared entry is checked: bare filename,
exists in the outputs directory, suffix in {`.md`, `.json`, `.html`},
within caps (§6). Two failure policies were considered:

- *Feed failures into the existing preview-repair loop*
  (`runner.py:620` re-prompts up to three times before stubbing).
  Rejected: an otherwise-valid preview with one oversize artifact
  would burn repair rounds and could end stubbed — losing a good
  summary over a bad attachment.
- *Drop the invalid entry, record the reason* — **chosen**. Artifact
  validation is independent of preview acceptance. Invalid entries
  appear in the manifest with `status: "dropped"` and a one-line
  reason; the preview lands normally.

Cancelled and errored runs get framework stub previews, which declare
nothing — an interrupted node publishes nothing, which is correct.


## 3. Reap-time pipeline and the store copy

At terminal transition, in the same step that stamps `commit_after`,
the runner:

1. Validates the declared entries against the workspace outputs
   directory (§2.2).
2. Copies each published file to
   `$MINICLAW_HOME/projects/<pid>/nodes/<nid>/artifacts/<name>`. The
   store directory is replaced wholesale on each reap (a rerun of the
   node republishes from scratch; no stale files linger).
3. Stamps the manifest onto the `Node` record and rewrites
   `node.json`, then emits the terminal `node_updated` so connected
   clients receive the manifest without a new event type.

The `Node` model (`domain.py`) gains one optional field:

```python
class ArtifactRef(BaseModel):
    name: str          # filename, unique per node
    bytes: int
    mtime: float
    sha256: str
    status: Literal["published", "dropped"]
    reason: str | None = None   # dropped entries only

class Node(BaseModel):
    ...
    artifacts: list[ArtifactRef] = Field(default_factory=list)
```

Additive and defaulted, so no store migration is required; the
existing upgrade discipline applies (sync before upgrading — an older
peer cannot parse records written by a newer one, which is already
the documented constraint).

Why the store copy matters: the workspace outputs directory lives in
the project checkout and never syncs, but
`projects/<pid>/nodes/<nid>/` is inside the single-writer project
subtree that metadata sync already carries. Published artifacts are
written there by the native machine only (reap runs only where the
node ran), so they merge trivially like every other node file. This
amends the sync design's "Artifacts do not sync" to: *workspace outputs
do not sync; published artifacts are node metadata and do*. The size
caps in §6 exist precisely to keep this sane — published artifacts
are text (`.md`/`.json`/`.html`), not build products.

A consequence worth stating: read-only machines get full artifact
*content*, not just tiles. The serving endpoint (§4) reads the store
copy, so it needs no `require_native_project` gate — artifacts are
more portable than diffs, which still require the native checkout.


## 4. Serving: one endpoint, two modes

```
GET /sessions/{sid}/nodes/{nid}/artifacts/{name}
GET /sessions/{sid}/nodes/{nid}/artifacts/{name}?raw=1
```

Both modes validate that `name` exactly matches a `published`
manifest entry on the node record (never a free path — no
enumeration, no traversal surface) and read from the store copy.

- **Default (JSON) mode** — feeds the inline panels. Returns
  `{name, text, bytes, mtime, sha256, truncated}` mirroring the
  `SessionFile` shape the CONTEXT.md viewer already consumes. `text`
  is truncated at the inline cap (§6) with `truncated: true`.
- **Raw mode** — feeds the new window for `.html` (and offers a
  plain view / download escape hatch for oversized `.md`/`.json`).
  Serves the file bytes with headers:

  - `.html` → `Content-Type: text/html; charset=utf-8` plus
    `Content-Security-Policy: sandbox allow-scripts` and
    `X-Content-Type-Options: nosniff`.
  - `.md` / `.json` → `text/plain` / `application/json`, same
    nosniff.

### 4.1 Why `CSP: sandbox` is non-negotiable

Artifact HTML is agent-authored — which means, transitively, that
anything the agent read (repo content, tool output, a prompt-injected
web page) can author it. Served plainly from the app origin, that
HTML would execute JS with same-origin access to the MiniClaw2 REST
API and browser storage: a script tag could delete projects or
exfiltrate transcripts. The `sandbox` CSP directive (without
`allow-same-origin`) forces the document into an opaque origin even
though the URL is same-origin: scripts run — interactive artifacts
like charts and demos work — but every credentialed or same-origin
request from inside the document fails. This is the standard
untrusted-content pattern and costs one header. In dev mode the Vite
proxy already forwards `/sessions/*` to the backend, so
`window.open("/sessions/.../artifacts/demo.html?raw=1")` works on
both origins.


## 5. Frontend: tiles, panels, and the new window

### 5.1 Canvas

A new React Flow node type `artifact` follows the `ContextNode`
visual language (stacked-card 160×70 tile, kind label + filename +
size line), with the suffix as the kind label (`MD` / `JSON` /
`HTML`). Published tiles hang below their producing agent node —
mirroring the `errorTerminal` offset pattern — fanned horizontally,
each connected by a new `produces` edge (rendered like `loads`, agent
→ artifact). Since artifacts are stamped only at terminal transition
and error/cancel stubs publish nothing, artifact tiles and error
terminals do not collide in practice.

At most **4** tiles render per node; more than that yields 3 tiles
plus a `+k more` overflow tile that opens the producing agent's panel
at its artifact list. Dropped entries never get tiles.

`CanvasSelection` (`Canvas.tsx:64`) gains one variant:

```typescript
| { kind: "artifact"; nodeId: string; name: string; ext: "md" | "json" | "html" }
```

`NodeInfo` (`types.ts`) mirrors the backend `artifacts` field, so the
layout pass derives tiles purely from data it already receives via
`node_updated` / `GET /sessions/{sid}/nodes` — no extra fetch to know
tiles exist.

### 5.2 Click behavior

- **`.md` / `.json`** — selection routes through `SidePanel` to a new
  `ArtifactPanel`, which fetches the JSON mode of §4 on demand.
  Markdown renders exactly like `PlanspaceFilePanel` (the project
  CONTEXT.md viewer): `react-markdown` + `remarkGfm` +
  `rehypeHighlight` inside `.md-prose`. JSON is pretty-printed
  (2-space indent; on parse failure, raw text) inside a highlighted
  code block. Truncated content shows a "showing first N KiB" note
  with an open-raw link.
- **`.html`** — the tile's click handler calls
  `window.open(rawUrl, "_blank")` directly. A canvas click is a
  genuine user gesture, so popup blockers permit it. Selection still
  lands on the `ArtifactPanel`, which shows metadata (name, size,
  hash, mtime) and a "Open window" button as the re-open path.

### 5.3 AgentPanel

The agent panel gains an **Artifacts** section listing every manifest
entry: published entries open with the same routing as their tiles;
dropped entries render greyed with their reason (`demo.html: exceeds
2 MiB cap`). This is the only place dropped entries surface — they
are a contract-feedback signal, not canvas content.


## 6. Limits

Published artifacts become synced git content and inline panel
payloads, so the caps are deliberately conservative. All are named
constants, adjustable without design change:

- `MAX_ARTIFACTS_PER_NODE = 16` declared entries; excess entries drop
  with reason.
- `MAX_ARTIFACT_BYTES = 2 MiB` per file; larger files drop.
- `MAX_ARTIFACTS_TOTAL_BYTES = 8 MiB` per node; entries past the
  budget (in declaration order) drop.
- `INLINE_TEXT_CAP = 512 KiB` — JSON-mode `text` truncation point;
  raw mode always serves the full file.
- Canvas fan-out cap: 4 tiles (3 + overflow), a display constant
  only.


## 7. What does not change

- **`materialize.py`** — the lane projection still copies the whole
  workspace outputs directory for downstream agents. The opaque
  agent-to-agent channel is untouched; publication is a parallel,
  narrower path to the human.
- **Reap's preview validation and repair loop** — artifact validation
  is a separate, non-fatal pass (§2.2).
- **The preview's semantic contract** — `motivation` / `summary` /
  `next_implications` remain the output; `artifacts` is an attachment
  list, not a result channel (§8).
- **Sync mechanics** — no new merge policy; published artifacts live
  in the already single-writer project subtree.
- **CONTEXT.md handling** — the files endpoint, `PlanspaceFilePanel`,
  and context tiles are unchanged; the artifact UI borrows their
  patterns rather than modifying them.


## 8. Philosophy alignment

PHILOSOPHY §8 makes two commitments this design must respect:

- *"There is no enum of output kinds."* Still true. Every node's
  output is still exactly one thing — the preview. `artifacts` does
  not classify the node or route its result; it attaches files to the
  one output that already exists. A node with no artifacts is not a
  different kind of node.
- *"Artifact paths and refs read are observed in the transcript, not
  declared on the preview."* (§8.1) This sentence is **amended** by
  this design, deliberately. The agent-to-agent artifact channel
  remains observed and opaque. But §8.2 already names a second
  consumer — "user-facing (horizontal)" context-out — and rendering
  for a human is precisely that. A publication for the user warrants
  a declaration: the agent, not a directory scan, decides what the
  human sees. PHILOSOPHY §8.1 should be updated alongside this
  change to read: *artifact paths read are observed in the
  transcript; artifacts published to the user are declared on the
  preview.*

The suffix whitelist is a rendering contract, not an ontology: it
constrains what the GUI can display (markdown, structured data, a
self-contained page), the same way `CONTEXT.md` — itself a special
global artifact with a dedicated viewer — constrains its format to
markdown.


## 9. Deferred

- **Live artifact updates during a run.** Decided against for now:
  one scan point at terminal transition, no watchers, no
  partially-written-file races. If long-running nodes make this
  painful, a rescan-on-`waiting` transition is the cheap extension.
- **Non-text artifacts** (`.png`, `.svg`, `.csv`, `.pdf`). The
  publication pipeline generalizes (the whitelist and per-type
  rendering are the only gates), but each type needs a rendering and
  sync-size decision; none is made here.
- **Multi-file HTML** (companion assets, subdirectories). The
  single-self-contained-file rule keeps serving, validation, and the
  sandbox story trivial. Revisit only with a real use case.
- **Inline HTML preview** (sandboxed `iframe` in the side panel) as
  an alternative to the new window. The window matches the decided
  UX; an iframe with the same sandbox policy is a pure addition
  later.
- **Artifact history across reruns.** Reap replaces the store copy;
  prior versions survive only in git history of `$MINICLAW_HOME`.
  Surfacing that history in the UI is out of scope.
- **Download / export bundling** (zip of a node's or lane's
  artifacts). Raw mode covers single-file needs.
