# 持久化格式迁移

MiniClaw2 通过发行包内的迁移单元升级持久化数据。当前目标版本和最低接纳版本只来自
`backend/miniclaw2/migrations/manifest.json`，不再由同步模块中的版本分支维护。
首个接入版本是 v15，布局重构在 v16 接入，v17 补回同事务内丢弃的有效布局；可信来源基线仍是 v14。v13 及更早的数据必须先使用覆盖其来源格式的中间版本升级。

## 使用与维护

程序更新后的首次存储打开自动执行无损迁移，不自动下载安装程序，也不联系远端。
所有迁移在临时快照中执行；校验成功后才发布，游标最后写入。
第二个进程不能同时打开同一物理存储根。首次部署前应停止仍在使用该根的旧程序；新版锁无法约束不遵守协议的旧客户端或手工 Git 操作。

```sh
miniclaw2 migrations status
miniclaw2 migrations plan
miniclaw2 migrations apply
miniclaw2 migrations recover
```

这些命令支持 `--root /path/to/store`。`status` 只读取原始标记与日志，不加载业务模型。
`recover` 在提交决定之前废弃暂存，在决定之后继续发布；它不是任意版本降级。
明确声明语义损失的脚本只能通过 `apply --accept-data-loss` 确认。确认覆盖当前发行窗口内声明的有损迁移契约，写入事务日志及当前机器、当前物理存储根的本机凭据，不随 Git 同步。
在线同步不会自动确认有损迁移；规范化远端或共同祖先时，可以复用本机已确认的同一契约。新增有损迁移仍需再次确认，复制目录不能继承原目录的确认。

如备份或暂存损坏、发现外部改写，恢复会停止，不猜测应保留哪个版本。可将原始备份导出到空的隔离目录：

```sh
miniclaw2 migrations recover --transaction TRANSACTION_ID --output /path/to/empty-recovery
```

输出分为 `store/` 和（如适用）`external_context/`。该操作不回滚活动数据、Git 历史或已经传播的新业务写入。
重新打开导出数据时仍需格式准入；复制目录不能继承原目录的本机完成凭据。
备份默认不会自动删除，代码支持窗口也不会删除用户数据或旧备份。

HTTP 的 `/health` 和 `/migrations/status` 在维护模式中仍可访问。
无法准入时服务不初始化 Registry、不调度节点；前端显示维护原因，而不是空项目画布。
修复或升级后重启服务，再重新检查页面。

## v16 节点布局切换

v15 迁移脚本已纳入发行摘要，不能重新定义其语义；布局变更因此使用独立的 `15 → 16` 迁移边。存储、API 和前端必须同批切换，旧 `/layout-hints` 写接口不保留兼容分支。

升级前先停止使用该存储的旧服务，检查影响后执行：

```sh
miniclaw2 migrations plan --root /path/to/store
miniclaw2 migrations apply --root /path/to/store --accept-data-loss
```

`plan` 的 `layout_impact` 按项目、host 和原始来源列出已有位置、将补回的位置、不恢复条目及 viewport，并区分缺失和空布局。v14 未分区记录同样计入；具体语义见 v17 小节。损坏文件或 v16 必须转换的非法 owner 坐标仍报错。

随后用新版程序重启服务，并刷新所有旧浏览器页面。不要对正在被旧后端使用的存储直接迁移。首次同步来自旧版设备的历史时，即使本机是新建 v16 存储，也可能需要通过上述确认授权规范化旧输入。

每个 `projects/<pid>/hosts/<owner>/layout.json` 转为同目录的 `node-layout.json`：

```json
{
  "schema_version": 1,
  "nodes": {
    "node-id": {"x": 320.0, "y": 180.0, "space": "planspace:implementation"}
  }
}
```

- 仅保留该 host 分区真实拥有的节点坐标；不从其他 host 的完整快照补缺。缺失位置由画布确定性布局补上。
- `space` 按显式 `planspace_id`、启动快照 `settings_snapshot.active_planspace_id`、父节点链依次解析；无归属或无归属循环使用 `canvas`。所属空间改变后旧坐标不再应用。
- 丢弃 foreign 副本、已删除节点、commit/ghost/planspace/context/template 等合成图元坐标和旧 viewport。这是有损取舍，必须确认；本地源文件原字节及权限保存在事务备份中。
- 跨 host 重复 node id、错误节点路径、非法 owner 坐标会使迁移失败，不猜测归属或悄悄覆盖。
- 同步在隔离区将本地、远端、共同祖先都规范化到当前版本，再三方合并。同步事务记录输入提交及确认契约；原始输入提交由合并历史保留，未规范化的旧快照不能直接混入活动树。

运行时从所有 owner 分片聚合 `node_positions`；普通项目更新不回写布局，新设备绑定只创建空分片。`PATCH /sessions/{sid}/node-layout` 接受 `updates` 与 `remove`，先完整校验再仅改本机分片；foreign、不支持的合成图元、未知节点及过时坐标空间返回 `409`，非有限坐标或缺失字段返回 `422`。已发布产出物按下节规则沿用生产节点的 owner。

真实节点及产出物只有本机 owner 可拖动。Git 卡片和 lane 按下节的独立持久化规则处理，其他合成图元仍由图结构与成员位置派生；viewport 按 `miniclaw2.canvas-viewport.v1:<project-id>` 保存在浏览器中，不进入 API、项目记录或 Git。只有用户 pan/zoom 保存视角，程序化居中和 fit 不覆盖它。

## v17 同事务布局补回

新增 `16 → 17` 非 destructive 步骤，不改写已发布的 v15/v16 脚本、摘要或 v16 的 destructive 声明。最低版本仍为 14，保留三条相邻迁移边。v14/v15 首次升级仍需 `--accept-data-loss`：转换包含 v16 有损中间步骤，随后 v17 在同一事务内补回；不借此变更启动或同步的确认策略。

`MigrationContext.input_records()` 读取 `source_root`，协调器将它指向本次事务的原始备份，而不是已被 v16 修改的暂存树。同步规范化同样先复制原始共享快照，再对远端、本地和共同祖先分别执行完整迁移链。v17 同时读取 v15 的 `hosts/*/layout.json` 和 v14 的 `project.json.layout_hints`，host 分片按路径排序优先，项目记录补缺；同一输入在不同执行机器上产生相同结果，不按 `machine_id` 选共享坐标。

- 真实节点和有效产物位置写入生产节点 owner 的 `node-layout.json`，包括只存在于其他 host 的副本；坐标空间由节点、设置快照及父链推导。
- 产物键按 `encodeURIComponent` 编码规则匹配，只接受已发布文件，跳过 op 产物，overflow 仅在已发布文件超过四个时保留。
- `commit:<sha>` / `commit:ghost` 写入项目 `git-layout.json`，`planspace:<id>` 写入 `lane-layout.json`，两者空间固定为 canvas。
- 所有目标只补缺，已有坐标（含升级后新拖动的位置）不覆盖，重复运行不改字节。合成图元和旧 viewport 按设计不恢复；悬空图元、非法标识和畸形／非有限补充坐标跳过并报告，不阻断其他有效位置的补回。

`plan` 在隔离副本演算中间步骤并复用 v17 的补回规则，活动文件不变。`layout_impact.restored` 分列节点、foreign 节点、产物、Git 和方向的计划补回数量；`retained` 表示目标已有位置或先前来源已提供位置；`not_restored` 和 `skipped_entries` 给出不恢复数量与原因，`viewport_discarded` 单列浏览器状态。多个来源包含同一图元时，仅首次有效补入计入 `restored`。

**v17 修复此后的升级，不自动找回此前已丢的数据。** 已是 v16 的存储，本次原始快照通常不再含旧布局；历史 `migration-backups/` 不属于迁移 SDK 的受管输入，也不随 host 同步。`plan.layout_recovery` 核对本机历史备份并列出仍缺失的位置和恢复预览命令；升级到 v17 后仍可查看此提示。已恢复的位置不再提示补回；无本机旧备份时明确提示到原升级设备查找，不能据此宣称恢复成功。

产物、Git 和方向继续使用下述恢复工具；真实节点缺失位置需核对备份后通过 `node-layout` 接口补入。若历史备份含 v14 未分区项目布局，现有恢复工具不能直接读取这部分数据，`plan` 给出隔离导出命令而不推荐无法完整恢复的分片恢复命令；请把示例输出路径换成实际空目录，再核对数据。历史恢复与新升级补回是两个独立路径，不回滚活动存储。

## 产出物布局与备份恢复

产出物的位置以 `artifact:<producer-id>:<encodeURIComponent(filename)>`、`artifact-overflow:<producer-id>` 为键，保存在生产节点 owner 的 `node-layout.json` 中，经 `SessionInfo.node_positions` 返回。坐标空间与生产节点一致；已发布但因折叠或数量聚合而隐藏的文件仍保留位置。已撤回／删除的产出物、已删除的生产节点及过时空间不再生效。“更多产出物”卡片仅在已发布文件超过四个时有效。拖动产出物不移动生产节点，也不修改文件内容或产出关系。

v16 的已发布迁移契约不改写；迁移曾丢弃的产出物坐标可从原始事务备份补回：

```sh
python -m miniclaw2.restore_artifact_layout --root /path/to/store --transaction TRANSACTION_ID --project PROJECT_ID
python -m miniclaw2.restore_artifact_layout --root /path/to/store --transaction TRANSACTION_ID --project PROJECT_ID --server http://127.0.0.1:8000 --apply
```

默认仅预览并列出来源及跳过原因；省略 `--project` 时检查所有仍存在的项目。逐文件校验布局与历史节点摘要，优先本机旧布局，再从其他 host 补缺；用历史生产节点与父链推导旧坐标空间，只恢复空间仍一致、仍已发布且由本机拥有的产出物。其他 owner 的位置需在对应设备恢复，未绑定项目不写入。

执行 `--apply` 前，需要有使用同一数据目录的新版后端正在运行；`--root` 不会启动后端。若服务尚未启动，先在另一个终端运行并保持运行：

```sh
MINICLAW_HOME=/path/to/store python -m miniclaw2 --host 127.0.0.1 --port 8000
```

若已有后端使用其他地址或端口，恢复命令的 `--server` 应与之匹配，不要为同一数据目录重复启动服务。`Connection refused` 表示目标地址未接受连接，应先检查后端是否启动以及地址／端口是否正确；连接中断或超时后可重试，已经保存的位置仍会保留。

`--apply` 通过正在运行的新版服务调用 `node-layout` 接口，不另开 Store、不覆盖活动文件、不回滚迁移或重启服务。请求使用 `only_missing: true`，在存储锁内仅补入当时仍缺失的坐标；预览之后新保存的位置也优先保留。该模式不允许 `remove`，不改变原有 owner 校验。可重复执行；旧服务不识别字段时拒绝请求。API 按正常布局保存流程持久化并安排元数据提交，前端重新读取会话后呈现；文件内容、Git／lane 布局与 viewport 不变。

## Git 布局与备份恢复

`projects/<pid>/git-layout.json` 是可选的项目共享记录，使用独立的 `schema_version: 1`，结构为 `{"schema_version": 1, "nodes": {"commit:<sha>": {"x": 10, "y": 20, "space": "canvas"}}}`。同时支持 `commit:ghost`。位置只接受有限数值，空间固定为 canvas；无文件／无条目时使用自动布局，手工拖动后保存，提交暂时不在展示集合内时不清理位置。

这是 v16 共享目录中的新增可选记录，不改写已发布的 v15/v16 迁移、根标记或旧节点布局，也不需要再次有损迁移。旧客户端不展示这些锚点，因此应升级后端并刷新前端后使用。文件随共享元数据备份和同步，校验器拒绝非法格式；同一锚点的并发修改或修改／删除冲突按完整坐标拒绝，避免 Git 文本合并把两台设备分别改动的 x/y 拼成未经确认的位置。其他文本合并冲突也保留显式失败，不自动选边。

`SessionInfo.git_positions` 返回共享坐标；`PATCH /sessions/{sid}/git-layout` 使用 `updates` 和 `remove`。已绑定设备可写，未绑定设备只读；执行节点仍只走 `node-layout`，不会混入 Git 坐标。保存失败会提示，不能把当前画面位置当成已落盘。

v16 曾删除的坐标通过原始事务备份恢复，不能把旧 `layout.json` 复制回活动树。先预览：

```sh
python -m miniclaw2.restore_git_layout --root /path/to/store --transaction TRANSACTION_ID
python -m miniclaw2.restore_git_layout --root /path/to/store --transaction TRANSACTION_ID --apply
```

工具逐文件核对事务记录的摘要，对仍存在的所有项目提取 commit/ghost 坐标；默认优先 `machine.json` 指定的本机 host，其他 host 按 id 排序补缺，可用 `--preferred-host HOST_ID` 指定优先来源。已存在的新坐标永远保留。

`--apply` 仅原子创建缺失的 `git-layout.json`，不覆盖已有文件；备份损坏时在写入前失败，并发创建时停止而不替换对方文件。可安全重跑；若已有文件仍有缺失条目，工具拒绝整批写入，应在新版服务中通过 `git-layout` 接口补入预览的差量。它不打开 Registry、不迁移或回滚活动存储、不触发同步、不提交 Git，也不改节点记录或 viewport。恢复后由新版后端下次会话读取呈现，随后按正常元数据同步流程传播。

## Lane 布局补充

`projects/<pid>/lane-layout.json` 与 Git 布局分离，结构为 `{"schema_version": 1, "nodes": {"planspace:<id>": {"x": -1704, "y": 3480, "space": "canvas"}}}`。这是同样的可选共享记录，不改写已发布迁移或根版本；通过 `SessionInfo.lane_positions` 返回，`PATCH /sessions/{sid}/lane-layout` 接受差量 `updates`／`remove`。已绑定设备可写，未绑定设备只读。同步按 lane 做结构化三方合并；同一 lane 在两端并发变化时采用发起合并一侧的值，避免可丢弃的界面坐标阻断业务数据同步。

拖动 lane 标题栏保存的是绝对坐标，内部节点依旧使用原有 parent-relative 坐标，不能把 lane 位移再加到子节点记录中。尺寸调整仍按成员边界计算，但不会移动已定位 lane；隐藏、焦点变化和列数变化也不清除锚点。没有锚点的 lane 使用自动排列，并避让已定位的 lane。

使用同一恢复工具添加 `--kind lane`，提取备份中的 `planspace:*`：

```sh
python -m miniclaw2.restore_git_layout --root /path/to/store --transaction TRANSACTION_ID --kind lane
python -m miniclaw2.restore_git_layout --root /path/to/store --transaction TRANSACTION_ID --kind lane --apply
```

默认优先本机备份、其他 host 补缺，对全部仍存在的项目恢复；校验、仅创建、不覆盖和可重跑规则与 Git 恢复相同。不会修改已有 `git-layout.json`、`node-layout.json` 或旧备份。

## 数据域清单

唯一文件归属规则位于 `migrations/inventory.py`，供暂存、备份、恢复与同步候选树使用。

| 域 | 路径及责任 | 完成凭据 |
| --- | --- | --- |
| shared | `projects/*/project.json`、`projects/*/git-layout.json`、`projects/*/lane-layout.json`；`projects/*/hosts/*/` 下的节点、事件、gate、预览、artifact、host/node-layout/head/git_aliases；根 `config.json`、`tags.json`、`.gitignore`；内置 `contextspace/` 的绑定、原则、planspace、模板与快照 | 根 `schema.json` |
| local | `projects/*/hosts/*/local.json`；凭据按本机身份与物理数据根绑定，不从远端复制 | `.migration-local/state.json` |
| external_context | 非默认位置的 `$MINICLAW_CONTEXT_HOME`，与受管共享路径不能重叠 | 该根自己的 `.migration-local/state.json` 和锁 |

`.git/`、`machine.json`、`machine.lock`、`.runtime-owner.json`、`.update-exit-pending`、
`.migration-local/`、`migration-backups/`、`workspaces/` 和 `*.tmp` 不属于共享业务快照。
项目源码、原生技能目录以及项目内 `.miniclaw2/graph` 和 `.miniclaw2/outputs` 不由此引擎转换。
事务日志与游标自身由引擎管理，不允许脚本写入。

事件转换逐行处理，断尾 JSONL 明确失败。确有转换的 `events.jsonl` 将原字节保留为
`events.jsonl.original`，另外还有事务备份；transcript、Markdown 和 artifact 正文不做内容转换。
受管树内的符号链接或特殊文件不接纳，以避免迁移与恢复越出数据根。

## 脚本与发布

每次格式演化新增 `migrations/steps/vNNNN_description.py`，声明一个 `MIGRATION`：

- 相邻的 `source → target`、受影响的 `scopes` 和格式 `contract`。
- 只操作原始数据及 SDK 的 `upgrade(context)`，以及该步骤的 `verify(context)`。
- 不导入历史业务模型、不读另一设备的 checkout、不写版本号、不负责 Git 或锁。
- 需要本机与共享域交接时，可通过 `input_records()` 读取本次事务原始快照；不能假设远端已经删掉的历史字段仍然存在。
- 专属回归用例放在 `backend/tests/migrations/test_vNNNN_description.py`。

```sh
python -m miniclaw2 migrations release --prune
python -m miniclaw2 migrations check
python -m pytest backend/tests/test_migrations.py backend/tests/migrations
```

发布工具自动生成清单与摘要。已在清单中的脚本不可修改；修正已发布转换应新增下一代脚本。
最低版本为 `max(14, target - 3)`，因此 v18 删除 14→15 的脚本和同名专属测试，仅保留三条迁移边。
生成结果必须随功能变更提交；CI 自动生成可审查的裁剪补丁，并拒绝源码与生成结果不一致。
wheel/sdist 构建同样校验清单，避免仅运行时停止导入，却把退役代码继续装入发行包。
本地开发中尚未发布的脚本与其清单摘要需要作为同一修改审查；不要修改已经发布的摘要来掩盖脚本变化。

## 同步与一致性

显式同步在活跃 runner 或终结写入未排空时延后，不强杀任务。
网页同步与初始化同步在工作线程执行；开始前要求其他存储请求及上下文刷新结束，
期间暂停新任务调度，业务 HTTP 请求返回 503，WebSocket 存储操作返回可重试错误。
健康检查和迁移状态仍可响应。远端探测、fetch 和 push 各有 30 秒超时；
请求取消不会提前释放仍在执行同步的维护门禁。
Git 定时提交、Store 访问和受管 ContextSpace 写入共享维护门禁。
同步先 fetch 到本地 Git 对象库，再在隔离目录规范化远端；分叉时也规范化共同祖先和本地快照。
只把完整校验后的合并结果作为事务发布，保留真实父提交关系。结构冲突不使用 `-X ours` 兜底。
新版、过旧、同代异契约或当前标记下混入旧记录的远端都不能进入活动数据树。

发布过程中保留原字节、权限、输入摘要和持久日志；文件替换及目录落盘使用 fsync。
跨文件、跨数据根的一致性依靠独占准入和恢复协议，而不是宣称多文件原子 rename。
提交决定后发现外部修改会保留维护状态。push 失败保留已验证的本地合并，不回退它，也不强推。
本地合并发布后即刷新 Store 索引，退出维护门禁前刷新 Registry，即使 push 失败也不沿用旧状态。
同步删除仅来自原 Git 树中已跟踪文件；被忽略的本机文件保留，远端同名异内容文件会触发冲突。
元数据校验按完整记录路径匹配，不把产物中的 `node.json`、`local.json` 等同名文件当作记录。

## 明确边界

- 现有引擎不承诺任意年代数据直升；不下载归档迁移器。
- 未知或无法确定归属的 v14 残留会失败；不会猜测路径或丢弃坏记录。
- 人工直接写文件、旧客户端及远端 Git 服务不受本机文件锁约束；需要服务端校验才能完全禁止不合规推送。
- 跨代脚本若需要早已从共享快照移除的输入，必须先设计明确的保留载体；当前没有自动生成跨设备交接数据的机制。
- 维护模式提供 CLI 恢复及说明，不提供网页中的备份选择、依赖安装或程序自动更新。
