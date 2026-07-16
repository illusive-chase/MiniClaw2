# Codex Review 与 MiniClaw2 AppServer

本文说明 MiniClaw2 当前的 review 能力，重点区分两件容易混淆的事情：

1. MiniClaw2 自己定义的 review 节点工作流；
2. Codex CLI 原生的 `codex review` 命令和 app-server `review/start` 方法。

## 结论

| 能力 | 当前状态 | 说明 |
| --- | --- | --- |
| Codex 执行 MiniClaw2 `agentic_review` 节点 | 支持 | review 节点通过普通 Codex `turn/start` 运行，review 提示词由 MiniClaw2 注入。 |
| Codex 执行 MiniClaw2 `human_interact_review` 节点 | 支持 | 先收集人的自由文本，再启动普通 Codex reviewer Agent。 |
| 直接运行 `codex review --uncommitted` / `--base` / `--commit` | 不支持 | MiniClaw2 没有启动 `codex review` 子命令的路径。 |
| 直接调用 Codex app-server `review/start` | 尚未接入 | 当前 Codex provider 只调用 `thread/start`、`thread/resume` 和 `turn/start`。 |

因此：如果需求是“让 Codex 审查 MiniClaw2 中某个节点的工作”，现在可以使用；如果需求是完整复用 Codex 原生 review 语义（审查未提交改动、某个 base branch 或某个 commit），当前 app-server 集成还不能直接使用。

## MiniClaw2 的 review 工作流

MiniClaw2 将 review 建模成 Agent 节点，而不是单独的 provider 命令。review Agent 必须包含：

- `category: "review"`
- `subtype: "agentic_review"` 或 `"human_interact_review"`
- 结构化 `brief`，包括 `check_what`、`expected`、`abnormal`
- 可选的 `scheduled_deps`，用于声明要审查的上游节点

领域模型对这些约束进行校验：

- [backend/miniclaw2/domain.py](backend/miniclaw2/domain.py#L293) 要求 review Agent 有 subtype 和 brief；
- `programmatic_review` 只能用于 `kind=verifier`，不是 Codex Agent review。

### Agentic review

`agentic_review` 会像普通 Agent 一样启动。MiniClaw2 将 review brief、上游节点的 preview/transcript/artifacts 路径和 preview 写入契约放入 launch prompt。Codex 负责检查上游工作，并写出自己的 `preview.json`；review 结果和计划变更由该 preview 及其 virtual-node mutations 表达。

实现和提示词：

- provider-neutral runner 流程：[backend/miniclaw2/runner.py](backend/miniclaw2/runner.py#L215)
- agentic review 提示词：[backend/miniclaw2/prompts/category_agentic_review.md](backend/miniclaw2/prompts/category_agentic_review.md#L1)
- 已落地的 review 设计说明：[IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md#L203)

### Human-interact review

`human_interact_review` 会先进入 `awaiting_human_input` 状态，向 UI 发出 `human_review_prose` interaction。用户提交的 prose 被保存到 `human-review.md`，之后才启动 reviewer Agent。Codex 仍然通过普通 `turn/start` 执行，reviewer 需要把 brief、上游证据和人工意见综合到自己的 preview 中。

实现和提示词：

- runner 的等待和继续逻辑：[backend/miniclaw2/runner.py](backend/miniclaw2/runner.py#L187)
- human-interact review 提示词：[backend/miniclaw2/prompts/category_human_interact_review.md](backend/miniclaw2/prompts/category_human_interact_review.md#L1)
- WebSocket 的 interaction 输入：[backend/miniclaw2/app.py](backend/miniclaw2/app.py#L1280)

## 当前使用方式

### 1. 创建 review virtual

通过 MiniClaw2 API 创建虚拟 review 节点：

```http
POST /sessions/{sid}/virtuals
content-type: application/json
```

```json
{
  "prompt_draft": "Review the implementation and identify correctness issues.",
  "category": "review",
  "subtype": "agentic_review",
  "brief": {
    "check_what": "Review correctness, edge cases, regressions, and missing tests",
    "expected": "No correctness defects and adequate test coverage",
    "abnormal": "Any concrete bug, regression risk, or untested critical path"
  },
  "scheduled_deps": ["待审查节点的 id"],
  "planspace_id": "当前 planspace id"
}
```

该接口位于 [backend/miniclaw2/app.py](backend/miniclaw2/app.py#L815)。`model_preset_id` 可以显式指定；省略时由项目/虚拟节点的模型预设继承逻辑处理。

### 2. Promote review virtual

```http
POST /sessions/{sid}/virtuals/{vid}/promote
```

对应 endpoint 位于 [backend/miniclaw2/app.py](backend/miniclaw2/app.py#L867)。Promotion 后，review 节点进入 MiniClaw2 的普通 NodeRunner 流程。

### 3. Codex 实际执行路径

当节点使用 Codex provider 时，执行顺序是：

```text
codex app-server --listen stdio://
  -> initialize
  -> thread/start 或 thread/resume
  -> turn/start（包含 MiniClaw2 review prompt）
  -> 读取 delta / tool / turn-completed 事件
  -> 校验并持久化 preview.json
```

Codex provider 的入口和 thread 建立逻辑见 [backend/miniclaw2/providers/codex.py](backend/miniclaw2/providers/codex.py#L35)；`turn/start` 调用见 [backend/miniclaw2/providers/codex.py](backend/miniclaw2/providers/codex.py#L79)。仓库中没有 `review/start` 调用。

## Codex 原生 review 的现状

Codex CLI 的原生命令形态包括：

```bash
codex review --uncommitted
codex review --base main
codex review --commit <sha>
```

本机验证的 `codex-cli 0.144.1` app-server schema 还包含 `review/start`，其 target 类型包括：

- `uncommittedChanges`：审查 staged、unstaged 和 untracked 改动；
- `baseBranch`：审查当前分支相对某个 branch 的改动；
- `commit`：审查某个 commit 引入的改动；
- `custom`：使用自由文本 review 指令。

该协议还区分 `inline` 和 `detached` delivery。这个 schema 是通过本机 Codex CLI 生成的，不属于当前 MiniClaw2 仓库文件；Codex 升级后应重新确认协议和兼容性。

MiniClaw2 当前没有以下能力：

- WebSocket/REST 消息来表达 `review_target`；
- UI 选择未提交改动、base branch 或 commit；
- provider 内调用 `review/start`；
- 保存和展示原生 review 的独立 `reviewThreadId`；
- 将原生 review findings 转换为 MiniClaw2 的 `preview.json`。

理论上可以在普通 Agent prompt 中要求 Codex 自己执行 `codex review` shell 命令（前提是运行环境有该 CLI 且权限/沙箱允许），但这只是嵌套的间接调用，不是 MiniClaw2 对原生 review 的集成：target 不会被结构化保存，review thread 不会被单独跟踪，输出也不会自动满足 MiniClaw2 的 preview 契约。

当前 WebSocket 客户端输入只有 `user_message`、`interaction_response`、`interrupt` 和 `replay_request`，见 [README.md](README.md#L346) 及 [backend/miniclaw2/app.py](backend/miniclaw2/app.py#L1230)。

## 为什么不能只替换一个 RPC 方法

原生 `review/start` 接入不是简单地把 `turn/start` 字符串改掉，至少有四个兼容问题：

1. **请求形状不同**：原生 review 需要 `target` 和 `threadId`，而普通 turn 需要文本 `input`。
2. **线程语义不同**：detached review 可能返回新的 `reviewThreadId`，需要持久化并关联到节点。
3. **用户入口不同**：MiniClaw2 目前的 `user_message` 没有 review target 字段，也没有专门 endpoint。
4. **输出契约不同**：MiniClaw2 的成功 Agent 节点必须写自己的 `preview.json`。runner 在 terminal reap 阶段会校验该 preview；如果原生 review 只产生 findings 而不写 preview，节点会被标记为 preview contract error。相关路径见 [backend/miniclaw2/runner.py](backend/miniclaw2/runner.py#L735)。

## 未来接入原生 review 的建议

如果需要完整复用 Codex 原生 review，建议按以下顺序实现：

1. **扩展领域和请求模型**：增加 review target（uncommitted/base branch/commit/custom）以及 inline/detached delivery。
2. **扩展 CodexProvider**：在创建或恢复 thread 后调用 `review/start`，记录 `reviewThreadId` 和 turn id，并复用现有事件读取与 approval 处理。
3. **扩展 FastAPI/WebSocket**：增加显式的 `start_review` 请求或专用 REST endpoint；不要把 review target 编码进普通用户 prompt。
4. **定义结果适配**：把 native findings 转换成 MiniClaw2 review preview，或者在 native review 完成后追加一次 `turn/start`，要求 Codex 写符合 schema 的 preview。
5. **补齐 UI 和测试**：增加 target 选择、review thread 展示、错误/取消处理，并覆盖 commit/base/uncommitted 三类目标。
6. **固定最低 Codex 版本**：`review/start` 属于 app-server 协议能力，应在 provider 启动或 initialize 后做能力检测，避免旧版 Codex 直接失败。

## 给后续 agent 的检查清单

阅读或修改这部分代码时，先回答以下问题：

- 需求是 MiniClaw2 review 节点，还是 Codex 原生 `review/start`？
- 结果最终是否必须写入 MiniClaw2 的 `preview.json`？
- 审查目标是依赖节点的产物，还是 Git working tree/base/commit？
- review 是否需要人工 prose gate，还是完全自动？
- 当前 Codex CLI 版本是否声明并支持 `review/start`？
- 是否需要新的 WebSocket 消息、持久化字段和前端控件？

在没有完成上述区分之前，不要把“`category=review` 节点可运行”误判成“Codex 原生 `codex review` 已经接入”。
