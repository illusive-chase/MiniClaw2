# MiniClaw2 Dead Code 与历史兼容层清理计划

> 状态：Proposed  
> 编写日期：2026-07-10  
> 适用基线：当前工作区（包含尚未提交的 model preset / schema v2 改动）  
> 目标读者：MiniClaw2 维护者、后端/前端实现者、负责持久化与升级策略的评审者

## 1. 背景

MiniClaw2 当前约包含 1.2 万行 Python 后端代码和 1.2 万行 TypeScript/React 前端代码。代码库仍处于快速演进阶段，若干已经被替换的领域模型、客户端函数、图组件和文档描述尚未删除；与此同时，为读取旧持久化数据、旧事件日志、旧 Codex app-server 协议和旧 HTTP 客户端而保留的兼容逻辑已经分散到领域模型、Store、Registry、Runner、provider adapter、前端组件和类型定义中。

这两类问题需要区别处理：

- **Dead code**：当前生产调用链不可达，且不承担明确的外部兼容职责。此类代码应尽快删除。
- **历史兼容层**：当前仍可能读取旧数据、旧协议或旧客户端请求。此类代码不能仅凭“内部不常用”直接删除，必须先定义 canonical shape、迁移路径、兼容窗口和退出条件。
- **运行时容错 fallback**：用于进程崩溃恢复、PTY/transcript 不确定性、WebSocket replay 等场景。此类逻辑不是历史兼容冗余，不属于本计划的默认删除范围。

本计划把已发现的问题转化为可执行的分阶段工作，目标是在不破坏现有用户数据和受支持 provider 版本的前提下，让当前 schema、wire protocol 和内部命名逐步收敛。

## 2. 当前基线与验证结果

审计时的验证结果：

- `npm run build`：通过。
- `python -m compileall -q backend/miniclaw2`：通过。
- 使用空临时 Store 运行 `python -m pytest backend/tests -q`：`291 passed, 9 subtests passed`。
- 使用默认 `~/.miniclaw2` 运行完整测试：8 个测试模块在 collection 阶段失败。
- 默认 Store 的失败原因：导入 `miniclaw2.app` 时执行全局 `app = create_app()`，进而构造 `ProjectRegistry` 和 `Store`；Store 对已经标记为 schema v2 的全库执行验证，发现一个仍缺少 `model_preset_id` 的旧项目后中止模块导入。
- TypeScript 的 `noUnusedLocals` 和 `noUnusedParameters` 已启用，因此前端主要问题不是未使用局部变量，而是未使用导出、对象内残余字段、未注册组件、失效 API wrapper 和旧 wire 类型。

在开始清理前，应把上述命令和结果记录为基线。每个阶段完成后至少重复对应的 focused tests；每个可合并批次完成后重复完整验证。

## 3. 目标

### 3.1 主要目标

1. 删除当前生产代码中可证明不可达的模块、类型、函数、常量、导入和前端组件。
2. 为持久化 schema、HTTP/WS wire protocol 和 provider protocol 定义唯一 canonical shape。
3. 把旧数据转换限制在明确的 migration/deserialization 边界，不让运行时业务代码长期携带多代 fallback。
4. 停止向新数据双写旧字段，确保兼容字段具备实际退出路径。
5. 将 Store migration 从模块导入和普通 Store 构造中解耦，恢复测试隔离和应用可诊断性。
6. 删除或弃用与当前领域不一致的 `/sessions`、旧 interaction shape、旧 template shape 等兼容公共面。
7. 同步 README、实现状态文档、前端注释和当前实际文件/路由/图节点。

### 3.2 非目标

本计划默认不做以下工作：

- 不重写 Claude native PTY/transcript 机制。
- 不删除 transcript fingerprint scan、EOF fallback、keybinding fallback 等运行时容错。
- 不改变 WebSocket replay 的语义。
- 不实现尚未落地的 fork/worktree、artifact graph node 或 review edge 产品功能。
- 不顺带重构与清理项无关的 UI、样式或 provider 行为。
- 不承诺兼容未被明确列入支持矩阵的仓库外 Python 私有 API 调用。

## 4. 清理原则

### 4.1 Canonical shape 优先

每类数据只能有一个当前写入格式。旧格式只允许出现在：

- schema migration 输入；
- versioned event replay 的反序列化输入；
- provider adapter 的供应商协议输入；
- 有明确到期日的 deprecated HTTP alias 输入。

业务层不得继续生成旧格式。

### 4.2 先停止双写，再删除回读

任何兼容字段的退出顺序必须是：

1. 定义 canonical 字段。
2. 新写入只写 canonical 字段。
3. migration 把旧字段转换为 canonical 字段。
4. 保留一个版本周期的只读 fallback（如确有必要）。
5. 确认没有新增旧字段数据。
6. 删除 fallback、旧字段和旧测试 fixture。

当前 `provider_session_id` / `cli_session_id` 不符合此原则：Runner 仍在双写，因此 `cli_session_id` 永远无法退出。

### 4.3 兼容必须可观测

兼容分支至少应具备以下一种可观测性：

- migration audit record；
- structured warning；
- deprecated response header；
- telemetry/counter（若未来引入）；
- 测试中明确的 legacy fixture。

不可观测的 fallback 无法判断何时安全删除。

### 4.4 私有符号不默认承诺外部兼容

以下划线开头且未从包 `__init__.py` 导出的函数，不应以“可能存在仓库外调用者”为由永久保留。确有外部调用需求时，应先升级为有文档、有测试的公共 API。

### 4.5 每个阶段独立可回滚

持久化迁移、wire protocol 变化和纯 dead-code 删除不得混在同一个不可分割的提交中。每个阶段都需要独立测试、清晰回滚边界和明确的前置决策。

## 5. 已确认的 Dead Code 清单

### 5.1 后端：可直接删除

#### D-BE-001：旧 `context.py` 模块

- 文件：`backend/miniclaw2/context.py`
- 当前状态：生产代码没有导入；仅 `backend/tests/test_context.py` 引用。
- 替代实现：`backend/miniclaw2/contextspace.py::compose_context_bundle()` 已直接读取项目根目录 `CONTEXT.md`。
- 操作：
  - 删除 `context.py`。
  - 删除 `test_context.py`，或把仍有价值的缺失文件、编码失败测试并入 contextspace 测试。
  - 删除 README 中对 legacy context helper 的描述。
- 风险：低。

#### D-BE-002：未使用的 `paths.py`

- 文件：`backend/miniclaw2/paths.py`
- 符号：`validate_project_relative_path()`。
- 当前状态：生产和测试均无引用。
- 操作：删除整个文件。
- 风险：低。

#### D-BE-003：旧 `ContextBundle` 领域模型

- 文件：`backend/miniclaw2/domain.py`
- 符号：`ContextBundle`。
- 当前状态：没有实例化；实际 context compose 返回 `ComposedContextBundle`。
- 操作：
  - 删除旧 Pydantic 模型。
  - 更新 `domain.py` 模块注释和 README 领域模型列表。
- 风险：低。

#### D-BE-004：未使用的 `NodeRemoved` Pydantic event

- 文件：`backend/miniclaw2/events.py`
- 当前状态：Registry 直接广播 `{"type": "node_removed", ...}` 字典，未实例化 `NodeRemoved`。
- 决策：
  - 推荐方案：Registry 改为通过 `NodeRemoved` 发事件，使协议模型成为唯一来源；若不准备统一事件构造，则删除该模型。
  - 不应继续同时维护“模型定义”和“裸字典实现”。
- 风险：低，但需要先决定事件模型是否承担 runtime validation。

#### D-BE-005：无调用 helper 和 dataclass

- `backend/miniclaw2/providers/base.py::dump_model()`。
- `backend/miniclaw2/providers/claude_native/spawn.py::SpawnArgs`。
- `backend/miniclaw2/claude_hook_bridge.py::_write_passthrough()`。
- 操作：删除符号及仅因这些符号存在的 import。
- 风险：低。

#### D-BE-006：无调用的 template legacy shim

- 文件：`backend/miniclaw2/templates/launcher.py`
- 符号：`_instantiate_lane()`。
- 当前状态：无生产和测试调用，真正实现为 `_stamp_lane()`。
- 操作：删除。
- 风险：低。该函数为私有符号，不承诺仓库外兼容。

#### D-BE-007：重复且未使用的 keybinding 常量

- 文件：`backend/miniclaw2/providers/claude_native/keybindings.py`
- 符号：`UNSUPPORTED_SUBMIT_MODIFIERS`。
- 当前状态：无读取；函数内部另有硬编码集合。
- 额外问题：常量把实际已支持的 `meta+enter` 列为 unsupported。
- 操作：
  - 最小清理：删除常量。
  - 可选改进：保留一个准确常量，并让判断逻辑复用它。
- 风险：低。

#### D-BE-008：未使用的 `Store.load_project()`

- 文件：`backend/miniclaw2/store.py`。
- 当前状态：Registry 启动时只使用 `list_projects()`；生产和测试均无调用。
- 操作：删除方法。
- 风险：低；删除前确认该 Store class 未被作为仓库外公共 Python library 发布。

#### D-BE-009：未使用 import

- `backend/miniclaw2/reap.py`：`ExecutedPreview`。
- `backend/miniclaw2/templates/serializer.py`：`ReviewSubtype`。
- 操作：直接删除 import。
- 风险：低。

### 5.2 前端：可直接删除或收窄

#### D-FE-001：未注册的 `ReviewsEdge`

- 文件：`frontend/src/canvas/edges/TimelineEdge.tsx`。
- 当前状态：没有导入、没有进入 `EDGE_TYPES`、graph builder 不产生对应 edge type。
- 操作：删除 `ReviewsEdgeImpl` 和 `ReviewsEdge`。
- 限制：本计划不实现 review edge 产品设计；若产品决定恢复，应另立功能任务。
- 风险：低。

#### D-FE-002：请求不存在路由的 bootstrap client

- 文件：`frontend/src/api.ts`。
- 符号：`bootstrapSessionContextSpace()`。
- 当前状态：无前端调用，且后端没有 `/sessions/{sid}/contextspace/bootstrap` 路由。
- 操作：删除函数；同步删除 README 对该路由的描述。
- 风险：低。

#### D-FE-003：未使用的 template detail client wrapper

- 文件：`frontend/src/api.ts`。
- 符号：`getTemplate()`。
- 当前状态：前端无调用；后端 detail endpoint 可能仍作为外部 HTTP API 使用。
- 操作：
  - 删除前端 wrapper。
  - 若 `TemplateDetail` type alias 因此无消费者，也一并删除。
  - 后端 endpoint 是否删除归入公共 API 审计阶段，不在此直接删除。
- 风险：低。

#### D-FE-004：残余 artifact 几何字段

- 文件：`frontend/src/canvas/layout.ts`。
- 字段：`LANE.artifactOffsetX` 完全无读取。
- `artifactOffsetY` 当前只用于 error terminal，不再表达 artifact。
- 操作：
  - 删除 `artifactOffsetX`。
  - 将 `artifactOffsetY` 重命名为 `errorTerminalOffsetY`。
  - 清理 App、types 和 README 中残留的 artifact-node 注释。
- 风险：低。

#### D-FE-005：不必要的模块导出

- `frontend/src/transcript.ts`：`appendServerEvent`、`seedAgentTurns`、`appendAssistantText`、`appendAssistantThinking`、`mergeAssistantActivity`、`finishAssistantTurn`、`appendAssistantError` 等仅文件内部使用。
- `frontend/src/components/UsageStrip.tsx::formatUsagePair()` 仅文件内部使用。
- 操作：删除不必要的 `export`，只暴露真实模块入口。
- 风险：低；属于公共面收窄，不改变运行时行为。

### 5.3 写入但无消费者的数据

#### D-DATA-001：未实现 fork lineage 字段

- 文件：`backend/miniclaw2/domain.py::Project`。
- 字段：`head_commit`、`parent_project_id`、`parent_commit`。
- 当前状态：除模型和文档外没有任何生产读写。
- 操作：
  - 若未来两个版本内不实现 fork/worktree，删除字段。
  - 如果决定保留，必须在实现状态文档中标为 reserved，并增加明确 owner 和落地里程碑。
- 推荐：删除。未来真实实现时通过新 migration 增加比提前冻结无语义字段更安全。

#### D-DATA-002：`Node.context_sources` write-only

- 字段定义：`backend/miniclaw2/domain.py::Node.context_sources`。
- 唯一写入：Runner snapshot context bundle 时生成 source path 列表。
- 当前状态：后端和前端均不读取；完整信息已经存在 context bundle snapshot。
- 操作：删除字段和 Runner 写入，以 `context_bundle_id` 指向的 snapshot 为唯一审计来源。
- 风险：中。需要检查现有磁盘 JSON 是否允许 Pydantic 忽略旧字段，并确认没有仓库外消费者。

#### D-DATA-003：Session response 派生/重复字段

- `SessionInfo.provider`：由 `model_preset_id` 唯一决定，前端不读取。
- `SessionInfo.planspace_view`：前端实际使用 `/contextspace` 响应中的同名字段。
- 操作：从当前 session/project response 中移除；若进入 `/projects` 新 API，可从一开始不暴露。
- 风险：中。需要公共 API 兼容策略。

## 6. 历史兼容层清单与收敛设计

### 6.1 C-SESSION-001：三代 provider session ID

当前字段链：

```text
sdk_session_id -> cli_session_id -> provider_session_id
```

当前问题：

- `Store._migrate_node_payload()` 仍把 `sdk_session_id` 懒转换为 `cli_session_id`。
- schema v2 migration 重复执行相同转换。
- Runner 对每个新 session 同时写 `provider_session_id` 和 `cli_session_id`。
- Registry、Claude、Codex、前端和 bundled verifier 继续执行 `provider_session_id or cli_session_id`。
- 因为新数据仍双写，兼容字段没有退出时间点。

目标 canonical shape：

```json
{
  "provider_session_id": "..."
}
```

执行步骤：

1. 扩展 schema migration：
   - `provider_session_id` 已存在时保持不变。
   - 否则从 `cli_session_id` 复制。
   - 再否则从 `sdk_session_id` 复制。
   - 删除旧字段或在 migration output 中明确清除。
2. Runner 停止写 `cli_session_id`。
3. 新 Node schema 删除 `cli_session_id`。
4. Registry 和 providers 只读取 `provider_session_id`。
5. 前端 `NodeInfo`、resume 判断、InspectDrawer 删除 `cli_session_id` fallback。
6. bundled verifier 更新为只读取 canonical 字段。
7. 删除 `Store._migrate_node_payload()`，避免 schema migration 与 lazy migration 双轨。
8. 增加 fixture 覆盖三种输入优先级和迁移幂等性。

验收标准：

- 新创建的所有 `node.json` 不含 `cli_session_id` 和 `sdk_session_id`。
- 旧三类 fixture 均能迁移并恢复 session。
- 运行时生产代码搜索不到旧字段名，旧字段名只允许存在于 migration fixture/audit 文档中。

### 6.2 C-MODEL-001：model preset schema v2 migration

当前新增的 `backend/miniclaw2/migrations.py` 负责 project、node、preview、user template 的 model preset 迁移、backup、audit 和当前 schema 全量验证。

当前风险：

- 每个 `Store()` 构造都运行 migration 或全库 validation。
- `miniclaw2.app` 模块导入会构造全局 app/Registry/Store。
- schema 文件与实际数据出现部分不一致时，应用连导入都失败，无法通过 API 诊断或修复。
- 单元测试会受用户真实 `~/.miniclaw2` 污染。
- 当前 schema 已 canonicalize `model_preset_id`，但仍持久化派生的 `provider`，并在 snapshot 中再次复制 preset 详情。

目标架构：

```text
CLI/application startup
  -> cheap schema version check
  -> explicit migration phase when version is old
  -> start app

Store(record load)
  -> validate only the record being loaded
```

执行步骤：

1. 把 migration invocation 从普通 `Store.__init__()` 中移出。
2. 在 CLI 启动入口执行显式 `ensure_store_schema()`。
3. 为 migration 增加 dry-run/repair 能力，至少能报告：
   - schema version；
   - 待迁移文件数量；
   - 不一致文件；
   - backup/audit 位置。
4. `create_app()` 允许注入 Registry/Store，测试不得隐式读取用户目录。
5. 评估是否保留模块级 `app = create_app()`：
   - 若为 Uvicorn import string 需要保留，应确保构造不触发磁盘全库迁移。
   - 更推荐使用 app factory 或在 `__main__` 中完成 dependency injection。
6. schema 已为当前版本时不再每次全库扫描；单条 load 由 Pydantic/专用 validator 验证。
7. 为“schema 标记当前但混入旧文件”提供显式 repair，而不是永久启动失败。
8. migration 完成后记录版本、changed files 和输入旧 shape 计数。

验收标准：

- `python -c 'import miniclaw2.app'` 不因用户 Store 内容执行全库迁移。
- API tests 不设置 `MINICLAW_HOME` 时也不会读取真实用户数据，或测试配置在 session 级强制隔离。
- migration 失败时输出可操作的 repair 指令。
- migration 保持幂等，第二次运行不改写文件、不创建重复 backup。

### 6.3 C-MODEL-002：`provider` 与 `model_preset_id` 双重持久化

当前 `Project` 和 `Node` 同时持久化 `model_preset_id` 与 `provider`，validator 又无条件从 preset 覆盖 provider。

目标：

- 持久化状态只保存 `model_preset_id`。
- provider 通过 catalog property/helper 派生。
- 若 wire/UI 需要 provider，在 response serialization 时增加派生字段，不进入磁盘 schema。

执行步骤：

1. 统计所有 `.provider` 消费者并分为：provider selection、显示、migration input。
2. Runner/provider factory 改为 `provider_for_model_preset(node.model_preset_id)`。
3. UI 可通过已加载的 model preset catalog 得到 provider；必要时 response 继续返回派生字段。
4. migration 读取旧 provider 仅用于推断 preset，输出不再写 provider。
5. 删除 Project/Node validator 中的 provider 覆盖逻辑。
6. 删除“provider 必须与 preset 一致”的持久化双字段校验，因为不再存在第二份状态。

验收标准：

- project/node JSON 中不再保存 provider。
- 同一个 preset 不可能通过第二字段产生矛盾。
- frontend provider label 和 provider adapter 选择行为不变。

### 6.4 C-TEMPLATE-001：三种 template model 声明

历史输入：

- `providers: [claude]`
- `model_preset_id: opus-4-7`
- `allowed_model_preset_ids: [opus-4-7]`

目标 canonical shape：仅 `allowed_model_preset_ids`。

执行步骤：

1. migration 继续负责旧 user template 转换。
2. bundled template CI/loader 立即拒绝旧字段。
3. migration 完成后，runtime loader 也只接受 canonical list。
4. 删除 loader 对单数 `model_preset_id` 的容忍。
5. 测试分别覆盖 migration 输入和 current loader 输入，不在同一 loader 中长期兼容多代格式。

### 6.5 C-CONTEXT-001：ContextSpace binding 多位置回退

当前 project binding 可能来自：

1. `Project.project_context_binding_id`
2. `settings_override["project_context_binding_id"]`
3. `settings_override["context_binding_id"]`
4. root path 自动匹配

目标：只把 `Project.project_context_binding_id` 作为显式选择；root path 自动匹配是否保留需产品决策，但不得与两个 settings key 长期并存。

执行步骤：

1. migration 将两个 settings key 吸收到 typed field。
2. 若 typed field 已存在且与 settings 冲突，迁移应失败并给出具体路径，不能静默选择。
3. migration 删除旧 settings key。
4. runtime `resolve_project_binding()` 删除旧 key fallback。
5. 对 root-path auto-match 做独立决策：
   - 保留时明确其仅是“未绑定项目的发现机制”；
   - 一旦发现并采用，应持久化 typed field，后续不再每次推断。

### 6.6 C-CONTEXT-002：Active planspace 多位置回退

当前 active planspace 可能来自：

1. `Project.settings_override["active_planspace_id"]`
2. binding manifest `active_planspace_id`
3. 只有一个 planspace 时自动推断

目标：新增 `Project.active_planspace_id: str | None` typed field，并以它为唯一当前选择。

执行步骤：

1. 新增 typed field 和 schema migration。
2. 规定冲突优先级并记录冲突；推荐不静默覆盖。
3. Registry 创建/切换 planspace 直接更新 typed field。
4. binding manifest 只描述 available plugs，不记录某个项目的 UI 选择。
5. 单 planspace 自动推断仅用于首次迁移/发现，采用后立即持久化。
6. 删除 `_string_setting(project, "active_planspace_id")` 和 binding raw fallback。
7. 简化 `describe_project_contextspace()`，避免同时返回 project、binding、resolved 三个近似 active 字段。

### 6.7 C-LANGUAGE-001：preferred language settings fallback

当前读取顺序：

1. `Project.preferred_language`
2. `settings_override["preferred_language"]`
3. `settings_override["language"]`

目标：仅使用 typed field。

执行步骤：

1. schema migration 吸收两个旧 key。
2. 冲突时 typed field 优先，但 audit 中记录被丢弃的旧值。
3. runtime `project_preferred_language()` 不再接收 Project-like `Any`，改为明确的 typed Project 或规范化字符串。
4. 删除 update path 中为旧 settings key 做的持续清理。
5. 更新现有 legacy tests，使其成为 migration tests，而不是 runtime fallback tests。

### 6.8 C-API-001：planspace `seed` / `user_seed` 双字段

当前 `/planspaces` 同时接受 `seed` 与 `user_seed`，而 `/planspaces/blank` 只接受 `seed`；前端分别发送不同名称。

目标：统一为 `seed`。

执行步骤：

1. 前端先统一发送 `seed`。
2. 后端暂时接受 `user_seed`，但对旧字段发 deprecation warning/header。
3. 一个兼容窗口后删除 `user_seed`。
4. 测试当前请求只使用 `seed`；单独保留一个 deprecated client test，直到删除窗口结束。

### 6.9 C-WIRE-001：ask-user 双格式响应

当前前端同时发送 canonical `response.answers` 和 legacy `updated_input.answers`。后端又接受对象、数组、标量和多个 carrier。

目标内部格式：

```json
{
  "response": {
    "answers": {
      "question_id": {
        "answers": ["value"]
      }
    }
  }
}
```

执行步骤：

1. 前端停止生成 `toLegacyAnswers()` 和旧 `updated_input.answers`。
2. `InteractionResponse` 明确区分：
   - 用户答案；
   - permission updated tool input；
   - prose review response。
3. Runner 内只传递 canonical response。
4. Codex/Claude adapter 在 provider boundary 转成供应商需要的格式。
5. replay/deserialization 层继续接受旧 event/response fixture，转换后业务代码不再看到旧 shape。
6. 删除 `_codex_user_input_response()` 对顶层 answers、标量和旧 updated_input 的长期容忍。

### 6.10 C-WIRE-002：human review prose 多 carrier

当前 `_extract_prose_response()` 接受：

- `message`
- `response.text`
- `response.prose`
- `response.message`

目标：当前客户端只发送 `response: {"prose": "..."}` 或一个明确命名字段，具体名称在实现前通过 ADR 决定。

执行步骤：

1. 选定 canonical carrier。
2. 前端 `GateReviewForm` 只发送 canonical carrier。
3. legacy replay adapter 转换旧 carrier。
4. Runner 删除多 carrier 扫描。

### 6.11 C-WIRE-003：legacy `checkpoint_review`

当前后端和前端 type union 仍接受 `checkpoint_review`，但生产只发 `human_review_prose`。

目标：旧 event 只在 replay upgrade 层出现，当前 `ServerEvent` union 不包含旧 variant。

执行步骤：

1. 给 event log envelope 增加或确认 schema version。
2. replay 读取旧 `checkpoint_review` 时转换为 `human_review_prose`。
3. 后端 `InteractionRequest` current model 删除旧 literal。
4. 前端 current `InteractionRequest` union 删除旧 literal。
5. 删除 App/panel 中对旧 variant 的分支。
6. 保留 versioned legacy fixture 验证转换。

### 6.12 C-CODEX-001：旧 Codex approval RPC

当前支持：

- 新：`item/commandExecution/requestApproval`
- 旧：`execCommandApproval`
- 新：`item/fileChange/requestApproval`
- 旧：`applyPatchApproval`

并维护两套 decision vocabulary。

此兼容层当前不能按普通 dead code 删除。退出前置条件：

1. 明确 MiniClaw2 支持的最低 Codex CLI/app-server 版本。
2. 启动时能检测版本或通过 initialize capability 判断协议。
3. CI 覆盖最低支持版本和当前版本。
4. 发布说明中公布旧版本停止支持时间。
5. 兼容窗口结束后删除旧 method handlers、`_codex_legacy_decision()` 和对应测试。

在退出前可以先做的整理：

- 把 method -> decision adapter 放入版本化/能力化的小模块。
- 避免 PermissionDialog 直接知道供应商 decision string。
- UI 只提交 provider-neutral `allow/scope/interrupt`，adapter 生成新旧 vocabulary。

### 6.13 C-API-002：`/sessions` 项目兼容命名

当前领域内部使用 `Project` / `ProjectRegistry`，HTTP 和前端继续使用 `/sessions`、`SessionInfo`、`sessionId`。README 明确称其为 compatibility layer。

目标：canonical API 使用 `/projects`、`ProjectInfo`、`projectId`。

执行策略取决于是否存在外部 API 用户：

- **无外部用户**：一次性重命名，更新前端和测试，直接删除 `/sessions`。
- **有外部用户**：
  1. 新增 `/projects` canonical routes。
  2. 前端立即切换 `/projects`。
  3. `/sessions` 成为只调用同一 handler 的薄 alias。
  4. 返回 deprecation header，并在发布说明给出删除版本。
  5. 兼容窗口结束后删除 alias。

必须避免同时复制两套 handler 实现。

### 6.14 C-UI-001：permission suggestion 多命名兼容

`PermissionDialog` 同时接受：

- `label` / `title` / `description`
- `updated_input` / `updatedInput` / `input`

这些字段可能来自不同 Codex 协议版本或内部旧 shape。处理方案：

1. provider adapter 把供应商 suggestion 转成 provider-neutral suggestion model。
2. React component 只消费一种 UI shape。
3. 旧字段兼容保留在 adapter，不进入通用前端组件。

## 7. 不在默认删除范围的容错逻辑

以下逻辑即使包含 fallback，也不应在本轮因“old behavior”直接删除：

- Claude native input 在 PID resolution 失败后的 sibling JSONL fingerprint scan。
- transcript offset 无法精确 retarget 时退到 EOF。
- 未配置或无法解析 keybinding 时使用默认 Enter。
- hook HTTP、runner gate、installed hook 的分层 timeout。
- WebSocket reconnect replay、per-node monotonic seq 和 `seq == 0` ephemeral event 特判。
- Registry 启动时把前一进程遗留的非终态节点修为 cancelled。
- 文件系统操作中的 best-effort cleanup、atomic write 和不存在文件容忍。

若未来要简化这些逻辑，应另立可靠性任务，并先提供故障注入测试。

## 8. 文档与注释清理

README 当前包含以下已经失效的设计：

- 不存在的 `backend/miniclaw2/artifacts.py`。
- 不存在的 `GatePanel.tsx`、`ArtifactPanel.tsx`。
- 已不存在或未实现的 Artifact/Phantom/Produces/Reviews 图节点或边描述。
- 不存在的 `/sessions/{sid}/contextspace/bootstrap` 路由。
- 不存在的 `/sessions/{sid}/nodes/{nid}/artifact` 路由。
- 把 `checkpoint_review` 描述成当前 server event。
- 把旧 output kind/result files 描述成当前主要输出 ontology。

前端注释残余：

- `SessionInfo.layout_hints` 仍以 `artifact:<nid>` 为 synthetic key 示例。
- App 中仍把 inspected node data load 描述为 event/diff/artifact/context bundle。
- layout 几何常量仍使用 artifact 命名表达 error terminal offset。

文档清理要求：

1. README 的文件树必须由当前 `rg --files` 核对。
2. 路由表必须与 `app.py` decorator 清单核对。
3. wire event 列表必须与后端 Pydantic union 和前端 TypeScript union 核对。
4. `IMPLEMENTATION_STATUS.md` 继续作为已落地能力的权威来源，但也必须删除已经完成退出的 legacy 说明。
5. 未来 proposal 文档可以描述未实现目标，但 README 不得把 proposal 当作现状。

## 9. 分阶段实施计划

### Phase 0：建立清理护栏

目标：在改变任何兼容行为前建立可重复验证和 schema fixture。

任务：

- 固定临时 `MINICLAW_HOME` 的完整测试命令。
- 增加测试级 fixture，保证 API tests 不读取真实用户 Store。
- 收集 schema v1、schema v2、部分迁移失败的最小磁盘 fixture。
- 明确受支持的 Codex/Claude CLI 最低版本。
- 决定仓库是否承诺外部 Python API 和外部 HTTP API 稳定性。
- 为每组 legacy shape 建立唯一测试 owner。

完成标准：

- 本地和 CI 测试不依赖用户主目录状态。
- 支持矩阵和兼容窗口有书面结论。

### Phase 1：纯 Dead Code 清理

范围：第 5 节中风险为低、不涉及持久化或 wire 行为的项目。

建议拆分：

1. 后端死模块/死符号/import。
2. 前端未注册组件/失效 wrapper/导出收窄。
3. 文档中对应的死文件、死路由和 artifact 注释。

完成标准：

- `rg` 搜索不到已删除符号。
- 前端 build 和完整后端测试通过。
- 不改变磁盘 schema 和 HTTP response。

### Phase 2：迁移框架与应用构造解耦

范围：C-MODEL-001。

任务顺序：

1. `create_app()` 支持注入 Registry。
2. API tests 使用临时 Store。
3. migration 从 `Store.__init__()` 移到显式启动阶段。
4. 增加 dry-run/repair/report。
5. 降低 current-schema 正常启动的全库扫描成本。

完成标准：

- 导入 app 不读取/迁移真实用户数据。
- 部分损坏 Store 能得到结构化诊断，而不是阻止所有模块导入。

### Phase 3：持久化字段收敛

范围：

- C-SESSION-001 session ID。
- C-MODEL-002 provider 派生字段。
- D-DATA-001 lineage 保留字段。
- D-DATA-002 context_sources。

建议使用新 schema version，而不是继续扩充 schema v2 的隐式行为。若当前 model preset v2 尚未发布，可以在发布前合并调整；若已经有用户写入 v2，则应发布 schema v3。

完成标准：

- 新 JSON 只包含 canonical 字段。
- legacy 字段只出现在 migration 和 fixture。
- migration 幂等且有 backup/audit。

### Phase 4：ContextSpace 与 language settings 收敛

范围：C-CONTEXT-001、C-CONTEXT-002、C-LANGUAGE-001。

任务顺序：

1. 为 active planspace 增加 typed field。
2. migration 吸收 settings/binding 旧值。
3. Registry 写路径改用 typed field。
4. runtime 删除旧 key fallback。
5. 简化 contextspace response 的重复字段。

完成标准：

- 单看 `project.json` typed fields 即可确定 binding、active planspace、preferred language。
- `settings_override` 只保存真正 provider/runtime override，不再承担历史 schema 仓库。

### Phase 5：交互 wire protocol 收敛

范围：C-WIRE-001、C-WIRE-002、C-WIRE-003、C-UI-001。

任务顺序：

1. 定义 provider-neutral canonical interaction models。
2. 前端只生产 canonical shape。
3. provider adapter 负责供应商转换。
4. replay upgrade 层处理旧 event/response。
5. current unions 删除 legacy variants。

完成标准：

- UI 不包含 Codex-specific decision vocabulary。
- Runner 不扫描多个 response carrier。
- 旧事件仍能 replay，但进入业务层前已转换。

### Phase 6：Template schema 收敛

范围：C-TEMPLATE-001。

完成标准：

- 所有 bundled/user current template 使用 `allowed_model_preset_ids`。
- runtime loader 不接受旧 provider-only/singular shape。
- 旧 shape 只由 migration tests 覆盖。

### Phase 7：公共 API 命名与 provider 版本窗口

范围：C-API-001、C-API-002、C-CODEX-001。

此阶段需要产品/API 兼容决策，不应与内部清理自动合并。

完成标准：

- `/projects` 或 `/sessions` 只存在一个 canonical 实现。
- deprecated alias 有明确删除版本。
- Codex 最低版本有检测和 CI 覆盖。
- provider legacy RPC 有可执行退出日期。

### Phase 8：最终文档与 schema 审计

任务：

- 重写 README 的 architecture/layout/wire protocol。
- 核对 `IMPLEMENTATION_STATUS.md`。
- 删除 migration 已完成且兼容窗口结束后的说明和代码。
- 运行全仓库 legacy/deprecated/old/fallback 关键词审计。
- 输出最终 canonical schema 和兼容矩阵。

完成标准：

- README 中所有文件、路由、节点、边和 event 均真实存在。
- 每个保留的 fallback 都有明确可靠性或兼容理由。

## 10. 决策记录要求

实施前至少需要形成以下 ADR 或等价书面决定：

1. **ADR-001：持久化 schema migration 生命周期**
   - 启动自动迁移、显式 CLI 迁移还是二者组合。
   - 支持多少个历史 schema version。
2. **ADR-002：Project/Node 是否持久化派生 provider**
   - 推荐只保存 `model_preset_id`。
3. **ADR-003：ContextSpace 当前选择的唯一所有者**
   - 推荐 Project typed fields。
4. **ADR-004：InteractionResponse canonical schema**
   - ask-user、permission、human review 的 provider-neutral shape。
5. **ADR-005：HTTP API 稳定性与 `/sessions` 退出策略**
6. **ADR-006：Codex CLI 最低支持版本和 capability negotiation**
7. **ADR-007：事件日志版本化与 replay upgrade 策略**

## 11. 测试计划

### 11.1 每次变更的最低验证

```bash
python -m compileall -q backend/miniclaw2
env MINICLAW_HOME=/private/tmp/miniclaw2-cleanup-test python -m pytest backend/tests -q
cd frontend && npm run build
```

测试目录应使用 pytest fixture 生成的唯一临时路径，固定 `/private/tmp` 命令仅用于手动验证，避免并发运行互相污染。

### 11.2 Migration 专项测试

必须覆盖：

- 空 Store 从无 schema 文件初始化。
- schema v1 provider-only project/node/template。
- `sdk_session_id`、`cli_session_id`、`provider_session_id` 三种输入。
- 三种字段同时存在且值冲突。
- 部分项目已迁移、部分仍旧。
- schema version 已升级但文件仍旧。
- ContextSpace root 通过 `MINICLAW_CONTEXT_HOME` 指向 Store 外路径。
- backup 和 audit 写入。
- migration 中途失败后的原文件完整性。
- 第二次运行幂等。
- 新版本程序拒绝读取未来 schema version。

### 11.3 Wire/replay 专项测试

必须覆盖：

- 当前 canonical ask-user response。
- legacy ask-user response 经 adapter 转换。
- 当前 human review response。
- `checkpoint_review` 旧事件 replay 转换。
- reconnect replay 不重复当前事件。
- `node_removed` 的 `seq == 0` ephemeral 行为保持不变。

### 11.4 Provider compatibility 测试

- Codex current approval methods。
- 最低支持版本使用的 legacy approval methods。
- capability/version 不支持时的清晰错误。
- UI provider-neutral response 到两套 Codex vocabulary 的转换。
- Claude ask hook passthrough 和 timeout ordering 不受清理影响。

## 12. 风险与缓解

| 风险 | 级别 | 缓解措施 |
|---|---:|---|
| 删除旧字段后无法恢复历史会话 | 高 | 先停止双写；增加真实旧 Store fixture；migration backup/audit；验证 resume |
| schema 标记与数据内容不一致 | 高 | 显式 repair；逐文件报告；禁止仅靠全局 version 跳过诊断 |
| 外部 HTTP 客户端仍使用 `/sessions` 或旧 payload | 中到高 | 先确认用户；alias + deprecation header + 删除版本 |
| 外部代码调用私有 Python helper | 低 | 不默认承诺私有 API；如有需求先转公共 API |
| Codex 旧版本失去 approval 能力 | 高 | 明确最低版本、版本检测、双版本 CI 后再删除 |
| replay 旧日志无法展示 | 高 | versioned replay adapter；保留 legacy fixture |
| ContextSpace 多来源迁移发生冲突 | 中 | 冲突时 fail-fast 并报告，不静默选择；保留 backup |
| dead code 清理误删未来 proposal | 低 | proposal 保留在 proposal 文档，不在生产代码预埋不可达实现 |
| 大批次难以定位回归 | 中 | 按阶段/主题拆提交，不混合格式化和无关重构 |

## 13. 回滚策略

### 13.1 纯代码清理

- 独立提交，可直接 revert。
- 不改变 schema，不需要数据回滚。

### 13.2 持久化 migration

- migration 前逐文件 backup。
- audit 记录每个改变的相对路径和 migration version。
- schema 文件最后写入，不能在数据迁移完成前提前标记新版本。
- 失败时不得自动删除 backup。
- 回滚程序需要明确是否支持向下读取；默认不假定新数据可被旧程序读取。

### 13.3 Wire/API 变更

- canonical handler 与 deprecated alias 共用同一实现。
- 兼容窗口内回滚前端不要求回滚后端。
- 删除 alias 的发布必须能通过重新启用薄 alias 快速恢复，不应需要恢复两份 handler。

## 14. 建议提交拆分

为降低评审和回滚成本，建议至少拆成以下提交/PR：

1. `chore: remove confirmed backend dead code`
2. `chore: remove stale frontend exports and graph remnants`
3. `docs: align architecture and route inventory`
4. `test: isolate app tests from user store`
5. `refactor: decouple store migration from app import`
6. `migration: canonicalize provider session ids`
7. `migration: remove persisted derived provider fields`
8. `migration: canonicalize project context settings`
9. `refactor: canonicalize interaction response schema`
10. `refactor: version legacy event replay`
11. `api: introduce canonical project routes and deprecate sessions`
12. `provider: retire legacy Codex approval protocol`（满足版本退出条件后）

不建议把上述工作压成一个大 PR。

## 15. 完成定义

整个清理计划完成时，应满足：

- [ ] 第 5 节所有确认 dead code 已删除或有书面保留理由。
- [ ] 新持久化数据不再包含 `sdk_session_id`、`cli_session_id`。
- [ ] `provider_session_id` 是唯一 provider conversation ID。
- [ ] Project/Node 不再持久化可由 preset 唯一派生的 provider，或有 ADR 明确保留理由。
- [ ] template current schema 只有 `allowed_model_preset_ids`。
- [ ] binding、active planspace、preferred language 都有唯一 typed storage location。
- [ ] 前端只生成 canonical interaction response。
- [ ] legacy event 只在 replay upgrade 层出现。
- [ ] Store migration 不在普通模块导入时扫描/改写用户数据。
- [ ] API tests 不受真实 `~/.miniclaw2` 影响。
- [ ] `/sessions` 和旧 Codex RPC 均有明确保留理由或已完成退出。
- [ ] README 文件树、路由表、wire event 和当前代码一致。
- [ ] 所有保留 fallback 都能归类为“当前支持版本兼容”或“运行时可靠性”，且有测试。
- [ ] 后端完整测试和前端生产构建通过。

## 16. 第一批建议执行范围

为了尽快获得收益且不触碰持久化兼容，第一批只建议执行：

1. 删除 `context.py`、`paths.py` 和对应旧测试/文档。
2. 删除 `ContextBundle`、`dump_model()`、`SpawnArgs`、`_instantiate_lane()`、`_write_passthrough()`。
3. 删除未使用 import 和 keybinding 重复常量。
4. 删除前端 `ReviewsEdge`、失效 bootstrap client、未使用 `getTemplate()` wrapper。
5. 删除/重命名 artifact 残余几何字段和注释。
6. 收窄只在模块内部使用的 TypeScript export。
7. 更新 README 的文件树、路由和当前 event 列表。

这一批不改变磁盘 schema、不改变 resume、不改变 provider 版本兼容，也不改变当前 HTTP 请求/响应，是最适合作为独立清理 PR 的范围。
