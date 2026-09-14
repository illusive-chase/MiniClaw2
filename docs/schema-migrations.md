# 持久化格式迁移

MiniClaw2 通过发行包内的迁移单元升级持久化数据。当前目标版本和最低接纳版本只来自
`backend/miniclaw2/migrations/manifest.json`，不再由同步模块中的版本分支维护。
首个接入版本是 v15，可信来源基线是 v14。v13 及更早的数据必须先使用覆盖其来源格式的中间版本升级。

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
明确声明语义损失的脚本只能通过 `apply --accept-data-loss` 执行，确认会写入事务日志。
在线同步不会自动确认有损迁移。

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

## 数据域清单

唯一文件归属规则位于 `migrations/inventory.py`，供暂存、备份、恢复与同步候选树使用。

| 域 | 路径及责任 | 完成凭据 |
| --- | --- | --- |
| shared | `projects/*/project.json`；`projects/*/hosts/*/` 下的节点、事件、gate、预览、artifact、host/layout/head/git_aliases；根 `config.json`、`tags.json`、`.gitignore`；内置 `contextspace/` 的绑定、原则、planspace、模板与快照 | 根 `schema.json` |
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
