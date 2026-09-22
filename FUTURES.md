# MiniClaw2 Futures

Where `PHILOSOPHY.md` states the destination and the code states the
present, this document holds the **gap between them**: design directions
not yet built, constraints the code cannot tell you about itself, and
places where the code currently contradicts the philosophy.

It exists because of a deliberate documentation rule: anything
discoverable by reading the code does not belong in prose, because prose
goes stale and code does not. What survives here is the residue — reasons,
risks, and unfinished intent, none of which a reader can recover from the
source.

Three kinds of entry, kept separate because they age differently:

- **Divergences** (§1) — the code and `PHILOSOPHY.md` disagree *today*.
  These are bugs against the design, and each one should eventually
  disappear by changing the code.
- **Latent hazards** (§2) — things that are true, load-bearing, and
  invisible at the place where someone would break them. These persist
  as long as the mechanism does.
- **Open directions** (§3) — design work that was argued through and not
  built. These disappear by being implemented or explicitly abandoned.


## 1. Where the code contradicts the philosophy

No known divergences are currently recorded.

## 2. Latent hazards

These are true of the current code and invisible where they matter. Each
one is a place where a reasonable change silently breaks something.

### 2.1 Adding a `Project` field without advancing the store schema

`Project` forbids unknown fields, and the project lister **skips records
that fail validation** rather than surfacing an error. Together these
mean: write a new `Project` field without advancing the store schema
version in the same commit, and an older build does not ignore the field —
it silently omits the entire project from the list.

The guard that appears to protect against this does not. A newer schema
version makes an older build open the store read-only, which blocks
*writes*; the lister keeps dropping records regardless. So the two costs
are inseparable: add the defaulted field **and** advance the schema
version together, always.

The migration window narrows what the *program* can do, not how long
backups live. Resist fixing "common ancestor too old" by reintroducing an
unbounded chain of historical migrators; a cross-schema merge that would
need an ancestor no release can explain should stop explicitly instead.
Note also that an older release does not obey a maintenance lock a newer
one introduced — stop old processes before the first switchover.

### 2.2 Compaction summaries are not turn boundaries

The transcript is the authority on *what the model did* in a turn — and
this is stated in the code, which is exactly what makes the adjacent
mistake easy. It is **not** the authority on *when the turn ended*. Only
the interactive stop hook is.

A transcript carries records that look terminal and are not, notably
context-compaction summaries. Treating one as a turn boundary ends the
node while the model is still working. The current translator handles only
conversation records and ignores everything else; that silence is
deliberate, not an oversight to be helpfully filled in.

### 2.3 Principles default to turn injection because providers are asymmetric

The default injection channel for principles is the turn channel, not the
system channel, and the reason is not visible at the line that sets it.
One provider re-applies system text on every spawn, so system injection is
durable there. The other can only simulate a system channel on the first
turn of a fresh thread — on a **resumed** thread, system-injected text is
silently dropped.

Turn injection is the only channel symmetric across both. The per-plug
override still exists, and choosing system injection means accepting that
silent-drop behavior on resume.

### 2.4 Skills are deliberately not plugs

Skills never enter bundle composition or bindings, and no skill body is
ever injected into a model's context — attaching one makes it *available*,
and lazy loading from its description is the provider's job.

This is enforced only by absence: the context layer has no awareness that
skills exist, and an id in the skill namespace would be silently dropped
rather than rejected. Nothing in the code says the omission is
intentional, which makes "helpfully" wiring skills into plug resolution an
easy and wrong change.

### 2.5 Version-pinned external protocols

Two capabilities depend on external CLI versions. Two are enforced in
code with named constants and tested; the third is not enforced at all —
the skill-root protocol call was verified manually once, and at runtime it
degrades on any exception. A silent regression there looks like "skills
just didn't load."

Separately, one mapping is pinned against the *shape* of another tool's
review findings. No test can catch a change in that shape, so it has to be
re-confirmed on upgrade by hand.

### 2.6 Skill materialization copies rather than symlinks

Expanded skill trees are copied into the per-node workspace instead of
symlinked. This is provisional: it is a cost accepted only because the
plugin loader has not been verified to follow symlinks. Nearby code
rejects symlinks during *import*, which is an unrelated safety concern and
actively misleads a reader into thinking that is the reason.

### 2.7 Push and pull are only one-directionally serialized

Push rejects an in-flight pull, but not the reverse: a pull can be spawned
while a push subprocess is still running. Push is deliberately not a
timeline op, so the quiescence check cannot see it. Known, accepted, and
not visible from either endpoint alone.

### 2.8 Derived state that must not become persisted state

Project activity time is an in-memory index derived from node timestamps
and is deliberately never written to disk. The reason is not in the code:
every value is rebuildable from nodes, so persisting it would buy nothing
and cost a git-tracked write on every state transition — plus merge
conflicts across hosts, for a cache.

The same principle governs commit hubs and edges generally: derive, never
mirror. Agents commit mid-run, users commit in terminals, rebases rewrite
hashes. Derivation turns every drift scenario into a rendering case
instead of a corruption case.

### 2.9 Neither remote is contacted unless the user asks

Two independent Git remotes back MiniClaw2 — the metadata store's and the
source checkout's — and both obey the same rule: a read derives from local
refs, and only an explicit user action fetches. `GET /global-state` and
`GET /self-update` perform no network IO; `POST /global-state/sync/check`
and `POST /self-update/check` are the sole fetch paths, one per remote.

This is invisible where it would be broken. Both surfaces look like
ordinary status reads, so a poll, a startup fetch, or a "refresh while the
panel is open" timer reads as a harmless freshness improvement — and each
would silently convert an explicit-consent design into ambient network
access. §12.5 forbids that for metadata; the source remote is held to it
because the operator's expectation is about MiniClaw2, not about which
repository happens to be behind a given number.

The cost is accepted knowingly: **"when was the remote last successfully
checked" is not answered anywhere.** It is the one fact here that Git
cannot express — `.git/FETCH_HEAD`'s mtime advances even when a fetch
fails, so it cannot stand in — and rather than persist a custom field for
it, the product declines the question. A never-checked checkout and one
checked a second ago with no new commits are therefore indistinguishable,
and both surfaces show the ref's own update time instead, which is a
different and honest fact.


### 2.10 Node identity is inherited by every descendant of the PTY child

`build_env` seeds the child from `os.environ.copy()`, so `MINICLAW_NODE_ID`
reaches not just the `claude` process MiniClaw2 spawned but every process
descended from it. Any of them running the user-level hooks in
`~/.claude/settings.json` therefore reports **the node's** id as its own —
including a `claude` session an agent starts from a Bash tool call, and
including one a human starts in a shell that happens to have inherited the
variable.

`turn_complete` no longer trusts that id alone: the `Stop` payload's
`session_id` must match a session the node's PTY is known to own, and an
unproven claim is dropped rather than accepted. The check is **fail-closed
on purpose**, and that choice has a visible failure mode worth stating: if
a CLI upgrade ever stops sending `session_id` in the `Stop` payload, no
signal can ever be proven, and every node runs to the 30-minute stall
timeout instead of ending. The single symptom is a `refused turn-complete`
warning per turn. Do not "repair" that by falling back to accepting an
unproven signal — that restores the original defect, in which a nested
session ended its parent's turn and stranded every tool call still in
flight.

The same inheritance reaches `session_ready`, which still reads
`MINICLAW_SESSION_ID` from the environment. That one is currently harmless
rather than fixed: its event is awaited once inside `start()`, before the
agent can run any tool, so a descendant's late duplicate only re-sets an
event that is already set. It stops being harmless the moment anything
awaits session-ready **after** the first submit — at that point it needs
the same payload-derived proof.

The general rule the two cases share: an inherited environment variable
names a node, it never proves one. Any future hook that lets the child
influence node lifecycle needs a credential the child cannot inherit.


## 3. Open directions

Design work argued through and not built. Each names the philosophical
commitment it extends, so a future reader can judge it against the
current destination rather than against the moment it was written.

### 3.1 The graph has no doctrine of forgetting

Project 与 lane 归档现已提供粗粒度的遗忘层：归档对象停止执行，并从默认节点载荷、
活跃扫描和索引重建中排除，同时保留全部历史且可 O(1) 恢复。这解决了“整项工作已经结束”
的场景，但没有解决活跃 lane 内部的新旧层次；下述时间性细节层级仍是开放方向。

The philosophy protects the *LLM projection* from accumulating noise and
insists a coherent summary is produced on demand, never maintained. The
*visual* projection has no equivalent discipline. "The graph is the state"
is total: state accretes forever, and a long-lived lane becomes an
archaeology site where the few tiles that matter are buried under
hundreds that do not. A chat at least scrolls old turns away; the canvas
keeps everything at equal weight.

This is the self-poisoning failure mode the philosophy already warns
about, relocated from the context window to the retina.

**Direction: temporal level-of-detail.** Completed causal chains collapse
into a single expandable capsule while the active frontier — running node,
ready virtuals, pending gates — always renders at full size. Nothing is
deleted; the durable store keeps everything. The philosophy's own
on-demand-summary stance supplies the mechanism: a collapsed label is
synthesized from its previews when needed and never maintained as durable
state. This becomes existential the first time someone runs a very long
lane. The same idea applied to the commit trunk rather than to lanes is a
separate, smaller version of it.

### 3.2 Plan mutations are invisible as events

The philosophy's sharpest idea — a reviewer's verdict *is* its graph
mutations — has no reading surface. After a planning or review node runs,
the virtual subgraph is silently different and the user must diff the plan
in their head. For a product whose thesis is that outputs are graph
mutations, the mutation itself is strangely not reviewable.

**Direction: plan-diff as an artifact.** Render the reap's
create/rewrite/obsolete set as a diff — "this review added two steps,
rewrote one prompt, obsoleted the deploy step" — as an expandable
annotation on the node that caused it; the proposer provenance needed for
this already exists. Two payoffs: verdicts become readable at a glance
(the existing post-run badge is the one-bit version of this view), and it
opens a natural future gate — *approve the plan change* before auto mode
acts on it. As agents write more of the plan, the user's role shifts from
author to editor, and an editor needs a diff, not a re-read.

### 3.3 The visual grammar promises concurrency the substrate qualifies

A dependency DAG with branches reads as "these run in parallel." Nodes
*can* now run concurrently up to the project limit, but they share one
worktree: they may observe each other's partial edits and conflict on
repository-wide operations. Nothing on the canvas communicates either the
actual ordering or that shared-worktree risk.

Forks are the sanctioned resolution — separate worktrees stacked as lanes
in one workspace view — and forks are the one major ontology piece never
built. Until then the tension is resolved by the user's confusion.

### 3.4 Finish the git metaphor — the missing verbs

The philosophy chose git IDEs as its analogy; taken seriously, that is a
roadmap. Mapping plan-space onto git verbs shows what is missing:

| Verb | Plan-space equivalent | Status |
|---|---|---|
| blame | proposer provenance | exists |
| revert | obsoletion with a reason | exists |
| cherry-pick | user templates capturing and stamping a subgraph | exists |
| diff | plan-diff (§3.2) | missing |
| branch / merge | forks across worktrees | named in the philosophy, unbuilt |
| rebase | re-anchoring a stack of virtuals onto a different upstream after a review reshapes the plan | missing; today it is manual dependency editing, tile by tile |

Forks are the big one. Rebase can follow once plan-diff exists to make
re-anchoring legible.

### 3.5 Alternate projections over the flat graph

The philosophy deliberately kept the graph flat *so that* views by time,
commit, or importance stay possible — an option purchased and never
exercised. The most valuable first projection is **cost and time**:
sessions take minutes and burn tokens, and the judgment "is this direction
earning its spend?" has no surface, even though per-node usage already
exists and simply never aggregates. A "by importance" view is essentially
an attention queue rendered spatially.

### 3.6 Deepen the direct-manipulation grammar

Edges are meaningful objects in this ontology, but the user cannot draw
one — creating a dependency means editing a list in a panel. Candidate
gestures: drag tile to tile to create a dependency; drop a tile onto a
tile to propose a review of it; drag a preview card across lanes as the
explicit "user as carrier" gesture for cross-direction transfer, which the
philosophy makes the user's sole responsibility and which is currently
high-friction.

The mindmap analogy was about node *creation*; the same principle extends
to relations. Dragging a library entry onto empty canvas to create a
pre-attached virtual is the small version of this, and the drop handler
already distinguishes empty canvas from a tile.

### 3.7 Artifact-forward tiles

The user validates *work*, not metadata. A finished tile's face today is
preview prose; for many nodes the honest face is the diff stat, the
produced file, the rendered document. Letting the tile lead with the
artifact — preview one click behind — moves the canvas from "status board
about the work" toward the work itself, which is where a graph *IDE*
should sit.

Agent 节点现已可选择 `diff_review`，并把运行起止两个工作树快照之间的
文件统计和可内联文本发布为 `miniclaw2.diff/v1` artifact。这提供了 tile
可直接消费的稳定 diff stat 数据；本节仍未完成的部分是让 tile 本身以该
artifact 为主视图，而不只是从 artifact tile 打开专用 viewer。

### 3.8 Remote execution keeps authority and access separate

A remote project has one authoritative Git worktree on the remote machine and
a one-way local projection for inspection. Its synchronized project record may
name that remote root and its repository fingerprint; SSH aliases, jump hosts,
credentials, and the local projection path are host-local facts and must remain
in the ignored `local.json`. The presence of that file remains the sole local
write-authority gate. Removing the gate would turn reachability into shared
state and recreate the consensus variable rejected by `PHILOSOPHY.md` §12.1.

The execution boundary must preserve three less obvious constraints.
First, every node in one project must resolve commits in the same repository;
otherwise the existing single commit graph and review staleness comparison mix
unrelated histories. Second, a source projection is remote-to-local only and is
never written back. Third, an agent's lane is a read/write protocol, not source
code: remote Codex execution must either retrieve its lane changes before reap
or expose local graph operations as explicit tools, without giving the remote
host a reverse connection to the backend. Git review still requires both remote
snapshots so concurrent changes remain visible as stale results.

远端执行仍依赖实验性的 Codex environment 协议，兼容性必须按明确验证过的
本机/执行器版本组合管理。仅有版本号不能证明工具可用，启动前还必须完成
ready、shell 和 thread cwd 回显检查；断线后的远端写入结果可能未知，不能
通过重新发送回合或去掉 environment 自动恢复。externalSandbox 的隔离由
远端容器或账号承担，而不是 Codex 提供的进程级沙箱。

`thread/resume` 的 environment 回显会混入配置文件环境和合成的 `local`
环境，因此握手只能按 `environmentId` 定位 MiniClaw2 注册项并逐字段核对，
不能要求回显列表只有一项。`thread/resume` 还会静默丢弃新传入的
`developerInstructions`；续接节点当前回合的框架契约必须走 turn 输入通道，
并明确替换前一节点契约。续接不会获得新传入的动态工具，工具集仍由原始
rollout 回放；工具集合的持久化比对尚未实现。

终态限流与断线不同：只有收到 `turn/completed(status=failed)` 且错误类型为
`rateLimitExceeded`、`usageLimitExceeded` 或 `serverOverloaded` 后，才可把后续
回合作为新操作自动续跑。默认开启，最多等待三次，固定间隔为 1、1、5 分钟；
等待期间节点进入可见的 WAITING 状态。SSH 建链失败仅对只读探针和读取操作按
1、2、4 秒重试；初始化、提交、push、pull/rebase 及执行中断线不自动重试。

workspaceWrite 的强制手段是 Codex 自带的 bubblewrap，它要求远端能创建
非特权 user namespace。禁止这一点的容器（AutoDL 的 Docker 实例即如此，
`docker-default` AppArmor + seccomp 拦下 `CLONE_NEWUSER`，且 `/proc/sys`
只读、无 root 可改）无法建成沙箱：Codex 不会失败退出，而是把每条命令都
升级成一次人工授权，表现为「必须逐条批准才能起命令」。因此启动握手除
版本外还必须回报一次沙箱自检结果，workspaceWrite 下自检失败即拒绝启动
并提示改用 externalSandbox。该判据取自沙箱能否建立，不能用内核的
`unprivileged_userns_clone` 值推断——AutoDL 上该值为 1 而 userns 依然被拒。
Codex 的 `features.use_legacy_landlock` 在此类容器中确实可用（Landlock ABI
可用且写入约束真实生效），但它只支持只读，与任何需要运行时强制的权限
配置互斥，故不能作为 workspaceWrite 的替代后端。

独立的 CONTEXT 初始化/刷新助手尚无远端控制通道，不能把本机源码投影当作
权威文件写入；远端 CONTEXT 目前需由普通 Codex 执行任务维护。源码投影只含
Git 跟踪文件，未 add 的新文件对本机 Claude 分析仍不可见。项目并发限制只
约束当前设备，多台设备仍可能同时写同一远端工作树。

### 3.9 Smaller deferrals with a stated reason

Kept because the reason, not the absence, is the content:

- **有损迁移的界面确认入口** — 先展示影响，再记录本机确认，不能把
  `--accept-data-loss` 直接包装成一个通用「确定」按钮。拟在设置面板显示
  有损步骤、`layout_impact`、`layout_recovery` 与导出备份路径；已拉取的
  远端快照影响应与本机影响分别展示，本机计划为空不等于远端无损。
  拟用 `/migrations/plan` 与 `/migrations/confirm`，读取计划不隐式 fetch；
  确认必须等待同步结束及运行任务空闲，再以同一存储准入屏障执行。
  仅当 shared、本机及外部 ContextSpace 游标均为目标版本时显示
  「仅补登本机凭据」；任一游标落后则展示实际迁移范围并明确确认数据丢失。
  确认绑定机器身份、已展示的版本与契约，状态变化必须重新展示，不静默扩大授权。
  命令行保留为后备；确认后重试同步仍需用户明确触发。该入口尚待产品确认，
  不随同步性能修复直接上线。凭据不跨机器传播：一台机器的知情同意不能代表
  另一台机器，改变这一边界需要单独决策。
- **Store compaction and retention** — transcripts grow the metadata repo
  monotonically. Deferred until repo size actually hurts, at which point
  archiving, compaction, or large-file storage are the options.
- **Encryption at rest** for partially trusted remotes. Note that the
  remote holds full transcripts, prompts, tool output, and code; the
  current answer is "use a private remote," which is a policy, not a
  mechanism.
- **A relational store** — the query that used to motivate migrating away
  from files (find everything awaiting a human, across all projects) now
  ships against the file store with an mtime-keyed cache. Deferred until a
  query arrives that this pattern cannot serve: one needing ordering or
  filtering across *all* nodes rather than the active subset plus a
  bounded recent window.
- **Cross-machine live streaming.** Viewer freshness is exactly as fresh
  as the last sync, and the read-only badge's "as of" timestamp is what
  keeps a stale running state honest. Relaying events host-to-host is a
  different feature, not an increment of sync.
- **Merging two non-empty stores** at bootstrap stays unsupported.
- **Detecting a disk clone that copied the hardware identity too.** When
  the OS machine-id or hardware UUID comes along with the image, no local
  signal distinguishes the copy from the original. Such a copy must run
  `machine copy` before first use. Automatic detection would need a
  trusted identity source that system imaging does not reproduce —
  hostname is not one.
- **Per-token streaming** for the transcript-driven provider, whose
  transcript is written block-at-a-time. Revisit only if that provider
  gains a partial-block stream.
- **Cost estimation.** Token counts are emitted; converting them to money
  needs per-model rates that change under us.
- **Review targets beyond the working tree** (a base branch, a commit, a
  custom range), which also answers reviewing committed ranges in
  auto-commit projects, where a working-tree review correctly
  short-circuits as clean.
- **Auto-converting review findings into fix nodes.** Deliberately not
  built until real reports show the structured findings are reliable
  enough to script against.
- **Review-blocking commits.** The review-then-commit sequence stays a
  workflow pattern the user composes, not a hard constraint on commit
  ops — and report-only stays the contract, so the reviewer never edits
  or comments on its own.

### 3.10 Explicit non-goals

Not "not yet" — decided against, recorded so they are not re-proposed:

- **Schema-generated review forms.** Human judgment is free-form by
  design; a form would re-impose the schema the review model exists to
  avoid.
- **Branch switching, merge-style pulls, and assisted conflict
  resolution.** The commit trunk stays linear and conflict resolution
  stays manual, deliberately.
- **Template nesting, and a run affordance in the template editor.** A
  template is a static definition; trying one out means instantiating it
  into a project.
- **Inferring template input ports from external dependencies.** Runtime
  node ids are not a stable, meaningful port interface, so ports are named
  explicitly by the author instead of generated.
- **Persisting template editor layout.** A template has no canvas of its
  own; inventing one would put view state into the on-disk schema.
- **User-reorderable project groups**, judged over-design.
- **Hard cross-host locks and cross-host concurrency limits**, which the
  substrate cannot express truthfully. Duplicates are detected and shown
  after the fact.
