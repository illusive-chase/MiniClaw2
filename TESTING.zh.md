# MiniClaw2 手动测试指南（Tier 1 + Tier 2 + Tier 3 + Tier 4 + ContextSpace）

本文档面向用户，介绍如何在仪表盘里逐个运行已经落地的内置测试场景，并手工
验证它们是否真的工作。本指南详细覆盖以下 9 个内置场景和 1 个 ContextSpace
手动流程：

- **Tier 1**（最小闭环）：`hello-text`、`bash-uname`、`write-readme`。
- **Tier 2**（内联 gate 与中断）：`permission-approve`、`plan-mode-approval`、
  `interrupt-midstream`。
- **Tier 3**（多步与上下文）：`context-md-respected`（§8）、
  `resume-fix-after-reject`（§9）。
- **Tier 4**（韧性）：`reconnect-replay`（§10）。
- **ContextSpace 手动流程**：bootstrap + bundle snapshot + 上下文注入（§11）。

Tier 3 的旗舰场景 `gui-calculator`（构建 PySide6/Qt 计算器 → 被动 review gate →
auto-commit 改写 `commit_after`）也已经在仪表盘里可用，但它的人工验收涉及
GUI 行为校验，内容相对独立，**本指南暂未为它专门写一节**；最新约定参见
`TEST.md §6 Tier 3`，运行流程与本指南覆盖的其它场景相同。

> **原则。** MiniClaw2 的测试不靠脚本"判定通过"。脚本（`verify.sh`）只是
> 程序底线；最终是否通过，**必须由你本人对照人工验收清单逐项打勾确认**。
> 哪怕是最简单的 `hello-text`，也不能跳过这一步。设计上的依据见
> `DESIGN.md §1.1`、`TEST.md §2`。

---

## 0. 前置准备

### 0.1 安装依赖并启动后端

```bash
cd backend
pip install -e .
python -m miniclaw2 --reload   # 监听 http://127.0.0.1:8000
```

环境变量：
- `MINICLAW_ANTHROPIC_MODEL`（默认 `claude-sonnet-4-6`）。
- `MINICLAW_HOME`（默认 `~/.miniclaw2`）：磁盘存储根目录；想隔离测试数据可以
  指向一个临时目录，例如 `MINICLAW_HOME=/tmp/miniclaw2-test`。
- **Claude provider**：使用本机 `claude` CLI 已配置好的鉴权。
- **Codex provider**：`codex` 必须在 `PATH` 上，且 `codex doctor` 能正常通过。

### 0.2 启动前端

```bash
cd frontend
npm install        # 首次需要
npm run dev        # 默认 http://127.0.0.1:5173
```

浏览器打开前端地址，你会看到顶部右侧有 `Claude/Codex` 选择器、`+ Node` 按钮、
以及 `Chat | Tests` 两个标签。Gate 节点不再由用户直接创建——在 `+ Node` 弹窗里
把 Output contract 选为 `review` 即可让 agent 写 brief，agent 完成后系统会自动
追加一个 passive gate 节点。

### 0.3 关于 provider 的说明

**每个场景都必须分别用 Claude 和 Codex 各跑一次，两个 provider 都通过才算这个
场景通过。** 这是有意为之的——provider 之间的差异本身就是有价值的信号
（适配层有问题 vs. provider 自身能力问题，都需要暴露）。

---

## 1. 打开测试面板

1. 在顶部 header 点击 **Tests** 标签。
2. 你应该看到十行场景（按从简单到复杂的顺序）：`hello-text`、`bash-uname`、
   `write-readme`、`permission-approve`、`plan-mode-approval`、
   `interrupt-midstream`、`context-md-respected`、`resume-fix-after-reject`、
   `reconnect-replay`、`gui-calculator`，每行右侧有 `Run · claude` 和
   `Run · codex` 两个按钮。
3. 点击任一按钮，前端会：
   - 调用 `POST /scenarios/<name>/run`；
   - 后端创建一个临时 git workspace（`/var/folders/.../miniclaw2-tmp-xxxx/`）；
   - 用 `temporary=true` 创建一个 Project，绑定 `scenario_name`；
   - 启动场景的第一个节点；
   - 前端切回 **Chat** 视图，时间线上会出现一个正在运行的节点。

> 💡 临时 workspace 是个一般化的特性，不只测试用。删除这个 session（DELETE
> /sessions/{sid}）时，workspace 目录会被一并清理。

---

## 2. 场景一：`hello-text`

### 2.1 它在测什么

最小闭环：WebSocket 协议、provider 适配层、assistant 文本渲染。不使用任何工具
也不写文件。

### 2.2 操作步骤

1. 在 Tests 面板，点击 `hello-text` 那一行的 `Run · claude`。
2. 等 1–5 秒，时间线上出现一个 agent 节点；右侧细节面板里逐渐出现 assistant
   回复（markdown 渲染）。
3. 节点进入 `done` 状态后，主聊天列下方会出现 **Verify** 卡片。

### 2.3 预期观察到什么

- assistant 回复是关于 Python 的一句话，**末尾带 `[OK]` 标记**。
- 没有任何 Bash / Edit 等工具调用记录。
- 时间线只有一个 agent 节点，状态 `done`。

### 2.4 跑 verify.sh

点 Verify 卡片左侧的 **Run verify.sh**。
- 期望：`exit 0`，stdout 显示 `ok`。
- 如果显示 `transcript missing [OK] marker`，说明模型没按要求带尾标——
  这是该场景的一种正常失败（模型不听话），不是框架 bug，但 acceptance 也应判否。

### 2.5 人工验收清单

右侧"Human acceptance"列出三条，请逐条核对并打勾：

- [ ] 回复像一句关于 Python 的正常人话（不是拒答、不是乱码、不是只有一个 `[OK]`）。
- [ ] 回复末尾能看见 `[OK]` 标记。
- [ ] 时间线里没有任何工具调用 tile。

**只有 verify.sh 通过 + 三项全勾上**，左上角才会出现绿色 `passed` 徽章。

### 2.6 然后切换 provider

切到 Tests 面板，点 `hello-text` 那行的 `Run · codex`，重复 2.2–2.5。两个
provider 都通过，此场景才算 PASS。

---

## 3. 场景二：`bash-uname`

### 3.1 它在测什么

Bash 工具链路：工具调用事件、`result_kind=stdout` 渲染、assistant 是否真的
基于工具输出生成回复（而不是凭空编一句）。

### 3.2 操作步骤

1. Tests 面板 → `bash-uname` → `Run · claude`。
2. 时间线出现 agent 节点，右侧面板里：
   - 先看到一个 Bash 工具 tile（可展开），里面是 `uname -a` 的真实输出；
   - 然后是 assistant 用一句话总结这是什么系统。

### 3.3 预期观察到什么

- **正好一次 Bash 调用**（不是两次，也不是零次）。
- Bash tile 展开后能看到形如 `Darwin <hostname> 24.6.0 Darwin Kernel ...` 或
  `Linux <hostname> 5.x ...` 的字符串。
- assistant 那句话和 Bash 输出一致（不能 Bash 显示 Darwin 却说"这是 Linux"）。

### 3.4 跑 verify.sh

verify.sh 会检查事件流里是否有 `activity` 事件 `result_kind == "stdout"` 且
包含 `Darwin`/`Linux`，并且 assistant 文本里也出现 `Darwin`/`Linux`。
- 期望：`exit 0`，stdout `ok`。
- 若 stderr 报 `no Bash stdout activity contained Darwin/Linux`：模型没用 Bash
  工具，或者用了但被另一种工具替代了。
- 若 stderr 报 `assistant text did not mention Darwin or Linux`：模型用了 Bash
  但没把内容写进答复，这也算失败。

### 3.5 人工验收清单

- [ ] 展开 Bash tool tile 能看到真实 `uname -a` 输出（路径式的、像系统真给的，
      不是 stub 或幻觉）。
- [ ] assistant 的总结句和 Bash 输出一致（不会把 Darwin 说成 Linux 或反过来）。
- [ ] 时间线里只出现了一次 Bash 工具调用。

### 3.6 切换 provider

同上，`Run · codex` 再跑一次。

---

## 4. 场景三：`write-readme`

### 4.1 它在测什么

Edit / Write 工具链路：工具能否真的把内容落到文件，节点 diff 面板能否正确显示
新增文件，模型能否严格遵循"只写这一行、不要别的"的精确规范。

### 4.2 操作步骤

1. Tests 面板 → `write-readme` → `Run · claude`。
2. 时间线出现 agent 节点；右侧面板里能看到一个 Edit / Write 工具调用。
3. 节点进入 `done` 后，切到右侧细节面板的 **Diff** 区域。

### 4.3 预期观察到什么

- 节点 diff 显示新增一个文件 `README.md`，内容恰好是 `# scratch`（加换行符）。
- 没有别的文件被创建（不应该出现 `.gitignore`、`notes.md`、`README.md.bak` 之类）。
- assistant 末尾用一句话确认它写好了文件。

### 4.4 跑 verify.sh

verify.sh 在 workspace 根目录下检查：
- `README.md` 存在；
- 内容严格等于 `# scratch\n`；
- `git status --porcelain` 里除了 `README.md` 没别的文件。

可能的失败：
- `README.md content mismatch`：模型多写了/少写了内容（最常见的是末尾多了
  解释段落、或者改成 `# Scratch` 大写、或者加了一行 `> placeholder`）。
- `unexpected files in workspace`：模型自作主张多建了文件。

### 4.5 人工验收清单

- [ ] Diff 面板里能看到 `README.md` 被新增，内容是 `# scratch`。
- [ ] assistant 的"我写好了"那句话不是谎话（diff 已经证实文件确实写了）。
- [ ] 没有额外被创建的文件。

### 4.6 切换 provider

同上。

---

## 5. 场景四：`permission-approve`

### 5.1 它在测什么

内联 permission gate 链路：当 project 处在 `permission_mode: default` 时，agent
调用 Bash 不会被自动放行，而是会触发一个 `interaction_request`
（`interaction_type=permission`）。你需要在 gate 标签页里手动 Allow 一次，
runner 才会继续，Bash 才会真的执行。这条路径如果挂了，所有需要人工审批的场景
都会跟着挂。

### 5.2 操作步骤

1. Tests 面板 → `permission-approve` → `Run · claude`。
2. 时间线上出现 agent 节点（脉冲蓝色 → 短暂之后变成绿色 `waiting`）。
3. 右侧细节面板会**自动切换到 `gate` 标签页**，里面是 Bash 工具的权限请求；
   工具命令应当是 `python3 -c 'print("hello-from-bash")'`。
4. 点击 **Allow**（"仅本次"）。
5. agent 继续运行，时间线下方出现 Bash 工具 tile，展开能看到
   `hello-from-bash` 这一行 stdout；最后 assistant 用一句话告诉你它打印了什么。
6. 节点进入 `done` 后下方出现 Verify 卡片。

### 5.3 预期观察到什么

- 在你点击 Allow **之前**，agent 一定停在 `waiting`，**不会**自己跑命令。
- Bash tile 展开后 stdout 正好是 `hello-from-bash`（不是别的、也不是空的）。
- assistant 的回复明确说出了那个字符串，没有幻觉或改写。

### 5.4 跑 verify.sh

verify.sh 在 `events.jsonl` 里同时找两样东西：
- `interaction_request` 且 `interaction_type == "permission"`；
- `activity` 且 `result_kind == "stdout"`、`result` 里包含 `hello-from-bash`。

可能的失败：
- `no interaction_request with interaction_type=permission found`：要么
  permission_mode 没生效（自动放行了），要么 provider 没把权限请求归一化成
  我们的事件名——属于适配层问题，记一笔后续修。
- `no Bash stdout activity contained 'hello-from-bash'`：你拒绝了权限，或者
  模型没真的用 Bash 去跑那条命令。

### 5.5 人工验收清单

- [ ] gate 标签页弹出过权限请求（你必须点 Allow，不是自动放行的）。
- [ ] Allow 之后时间线出现 Bash 工具 tile，stdout 内容是 `hello-from-bash`。
- [ ] assistant 的回复准确描述了打印的内容（不是拒答、不是幻觉）。

### 5.6 切换 provider

`Run · codex` 再跑一次。如果 Codex 在 `permission_mode: default` 下没有触发
权限请求（而是直接放行），verify.sh 会判负 —— 这是适配层信号，记下来反馈，
不要在 acceptance 强行打勾。

---

## 6. 场景五：`plan-mode-approval`

### 6.1 它在测什么

Plan-mode 链路：project 用 `permission_mode: plan` 启动，agent 必须先给出方案
并等待 `plan_approval` 批准，批准后才真正执行写文件。这是把"先看草案再放行"
的工作流，在 UI 上跑通的最直接证据。

### 6.2 操作步骤

1. Tests 面板 → `plan-mode-approval` → `Run · claude`。
2. agent 节点出现并进入 `waiting`；右侧 `gate` 标签页里能看到 plan-approval
   类型的请求，里面列出 agent 想写的文件（`PLAN_OK.txt`）和内容
   (`plan-approved`)。
3. 在 plan 对话框点 **Approve**（接受方案，进入 `acceptEdits` 模式）。
4. agent 继续运行：时间线出现 Edit / Write 工具 tile，把 `PLAN_OK.txt`
   写进 workspace 根目录。
5. 节点进入 `done` 后切到右侧 **Diff** 区域确认文件确实落盘了。

### 6.3 预期观察到什么

- 没批准之前，**不会**有任何文件被写——time line 里看不到 Edit/Write tile。
- 批准后写出的 `PLAN_OK.txt` 内容**严格**是 `plan-approved`（一行，加换行），
  没有别的解释段落、没有大小写改动。
- 整个 workspace 只新增了这一个文件。

### 6.4 跑 verify.sh

verify.sh 检查：
- workspace 根有 `PLAN_OK.txt`、内容等于 `plan-approved\n`；
- `git status --porcelain` 里除了 `PLAN_OK.txt` 没别的；
- `events.jsonl` 里至少出现过一次 `interaction_request` 且
  `interaction_type == "plan_approval"`。

可能的失败：
- `PLAN_OK.txt content mismatch`：模型多写了/改了大小写/加了注释行。
- `unexpected files in workspace`：模型自作主张多建了文件。
- `no interaction_request with interaction_type=plan_approval found`：plan 模式
  没触发 plan-approval 请求——可能是 provider 适配层问题（Codex 在 plan 模式下
  的事件归一化没接进来），或者后端 plan-mode 配置没生效。

### 6.5 人工验收清单

- [ ] gate 标签页里出现过 plan-approval 请求，你必须点 Approve（不是自动放行）。
- [ ] Diff 面板能看到 `PLAN_OK.txt` 新增、内容是 `plan-approved`。
- [ ] assistant 的确认句没说谎（diff 已经证实文件确实写了）。
- [ ] 没有额外被创建的文件。

### 6.6 切换 provider

`Run · codex` 再跑一次。Codex 是否对 `permission_mode: plan` 给出
plan_approval 风格的请求，本身就是这条 verify 的检验项之一。

---

## 7. 场景六：`interrupt-midstream`

### 7.1 它在测什么

中断（Stop 按钮）链路：agent 在跑一条 60 秒的 Bash 循环，你在中途点 Stop，
要求节点干净地进入 `cancelled` 状态，而且**已经流到磁盘的部分输出不能被
事后清空**。这是"我的 stop 是不是真把模型按住了、partial 数据是不是没了"
的核心回归。

### 7.2 操作步骤

1. Tests 面板 → `interrupt-midstream` → `Run · claude`。
2. agent 节点开始 streaming：右侧 Bash 工具 tile 里能看到
   `line 1` / `line 2` / `line 3`…… 一秒一行往外吐。
3. **等到看到至少三四行之后（大约 3–5 秒）**，点聊天输入框旁边的 **Stop**
   按钮。
4. 节点应当迅速变成 muted-grey（`cancelled`）。
5. Bash tile 里**已经出现过的那些行**应当原封不动地保留下来，
   不会被替换、不会被清空。
6. 节点终态后下方出现 Verify 卡片（cancelled 也是一种终态）。

### 7.3 预期观察到什么

- Stop 之后没有任何**新的** assistant 文本或工具调用涌出来——agent 真的停了。
- 节点 tile 颜色是 muted-grey（cancelled），**不是**红色（error）也不是
  绿色（done）。
- 已经存在的 `line N` 不会消失。

### 7.4 跑 verify.sh

verify.sh 从 `MINICLAW_HOME` 里翻出该 project 最新的 `node.json` 检查：
- `state == "cancelled"`；
- 同节点的 `events.jsonl` 里至少有一个 `text_delta` 或 `activity` 事件（证明
  在你点 Stop 之前确实流出了一些内容；如果这里是空的，说明 partial buffer 被
  cancel 清掉了——这是我们要防的回归）；
- `events.jsonl` 里**没有** `turn_done` 且 `state=="done"` 的事件（如果有就说明
  你 stop 晚了，循环自己跑完了，这个场景这次就不算"中断"测试，需要重跑）。

可能的失败：
- `latest node ... state is 'done', expected 'cancelled'`：你 Stop 太晚或者
  没点。重跑。
- `events.jsonl has no text_delta or activity`：你 Stop 太早（第一行还没流出
  来），或者更严重——cancel 把已经写过的事件抹掉了。两种情况之一，请仔细
  核对。
- `events.jsonl has a turn_done with state=done — node was not actually
  interrupted`：循环跑完了，stop 没生效或者你没点。

### 7.5 人工验收清单

- [ ] 你在 3–5 秒（看到几行之后）点了 Stop——不是没点，也不是等循环跑完。
- [ ] 节点 tile 是 muted-grey（cancelled），不是红色 error。
- [ ] 点 Stop **之前** Bash tile 里出现过的内容（`line 1`…）在 cancel 之后
      依然在那里，没被清空。
- [ ] Stop 之后没有新的 assistant turn 出来"自我总结"。

### 7.6 切换 provider

`Run · codex` 再跑一次。注意 Codex 的 stop / interrupt 链路是独立适配的，
两个 provider 都必须能干净 cancel 才算这条场景过。

---

## 8. 场景七：`context-md-respected`（Tier 3）

### 8.1 它在测什么

`<project_root>/CONTEXT.md` 的 provider-中立注入路径：场景启动时 backend 会把
`seed/CONTEXT.md` 拷贝到临时 workspace 根目录；`NodeRunner` 在 launch 时调用
`load_project_context()` 读出来，Claude 走 `system_prompt.append`、Codex 走
`turn/start` 输入预置；同一份文本会被快照到 `Node.system_context_snapshot`
字段以供事后审计。这条路径若挂了，所有依赖项目级上下文（行为约定、术语
词表、风格规范）的下游场景都会跟着挂。

### 8.2 操作步骤

1. Tests 面板 → `context-md-respected` → `Run · claude`。
2. 时间线出现一个 agent 节点，prompt 只是「2 + 3 等于多少？」这种家常算术题
   ——上下文里的「每次回复都要以 `[CTX-OK]` 结尾」才是实际信号。
3. 等几秒，节点进入 `done`；右侧 Chat 面板里的 assistant 回复应当在末尾带
   `[CTX-OK]` 标记。
4. 切到右侧 **Settings** 标签，确认 `system_context_snapshot` 段不为空、
   内容就是 seed 里那句话（同时它也作为 `CONTEXT.md` 出现在 workspace 根）。

### 8.3 预期观察到什么

- assistant 给出了 2 + 3 = 5 的简单答复（不是拒答、不是别的题目）。
- 回复**末尾**带有 `[CTX-OK]` 标记，且整段没有任何工具调用 tile。
- Settings 标签里 `system_context_snapshot` 显示的字符串和 seed 那句话一致。

### 8.4 跑 verify.sh

verify.sh 做两件事：
1. 拼接所有节点的 `events.jsonl` 里的 `text_delta`，断言里面包含 `[CTX-OK]`。
2. 遍历所有 `node.json`，断言至少有一个节点的 `system_context_snapshot`
   **逐字节等于** workspace 根的 `CONTEXT.md`（防止 loader 路径读到空字符串
   或读串行）。

可能的失败：
- `transcript missing [CTX-OK] marker`：模型没听上下文（可能 prompt 太霸道、
  也可能 provider 适配没注入成功）。
- `no node carried a system_context_snapshot matching CONTEXT.md`：注入路径有
  bug，更严重——回归级别。

### 8.5 人工验收清单

- [ ] assistant 的回答给出了 2 + 3 = 5（不是拒答、不是答非所问）。
- [ ] 回复末尾出现 `[CTX-OK]` 标记。
- [ ] 时间线里没有任何工具调用 tile。

### 8.6 切换 provider

`Run · codex` 再跑一次。Codex 是把 CONTEXT.md 内容 prepend 到 `turn/start`
输入上的；如果 Codex 端拿不到 marker 但 Claude 端拿得到，说明适配层在 Codex
分支上漏了 context 注入——记下来反馈，不要在 acceptance 强行打勾。

---

## 9. 场景八：`resume-fix-after-reject`（Tier 3）

### 9.1 它在测什么

resume 边 + scenario 分支：三个节点 `build → review → fix`。`build` 故意只
写 `mathutils.py::add` 并产出一份 review brief；`review` 是被动 gate，把
brief 渲染给人看；你必须以 `{"approved": false, "notes": "..."}` 形式 reject。
scenario expander 把 review 的 `decision: "rejected"` 写进 history,匹配
`fix` 节点的 `when: review.rejected` 谓词,启动 `fix` 节点并通过
`resume_from: build` 把 `build` 的 provider 会话继承下来——所以 fix 就像「同
一个 agent 接着写」一样能读到先前的对话上下文。这条路径串起来后,以下回归都
会一并暴露：

- `Node.review_outcome` 是否正确从 `{approved: bool}` 推出 `approved`/`rejected`;
- `scenario_step_history` 是否落上 `decision`;
- expander 的 `when:` 跳过逻辑是否正确;
- `resume_from_node_id` 是否真的把 `provider_session_id` 一路接过去;
- 时间线 SVG 是否把 resume 连线和 `↻ build` 角标画出来。

### 9.2 操作步骤

1. Tests 面板 → `resume-fix-after-reject` → `Run · claude`。
2. `build` 节点开始 streaming，最终会做两件事：
   - 在 workspace 根写出 `mathutils.py`,**只**导出 `add(a, b)`;
   - 在 `.miniclaw2/outputs/<build-id>/brief.md` 写出一份三段式 review
     brief（`# How to run` / `# What to verify` / `# Response schema`）,
     指明审阅者该如何 import 该模块并填什么样的 JSON。
3. `build` 完成后,auto-commit op 节点会被自动追加,提交这次改动。
4. 紧接着出现 `review` 被动 gate 节点,右侧面板自动切到 `gate` 标签——里面
   逐字渲染 build 写的 brief（**不是模板**,是 agent 现场写的）。
5. 在 gate 表单里选 **write-json**,路径填 `reviews/build.json`(scenario
   预设值,通常会自动带上),内容写：
   ```json
   {"approved": false, "notes": "请再加一个 subtract(a, b)"}
   ```
   （`notes` 可换成别的合理要求，例如「加 multiply / divide」；fix 节点会按你
   写的来。）
6. 提交后 review 节点变 `done`，scenario expander 会：
   - 落上 `decision: "rejected"` 到 history;
   - 匹配 `fix` 节点的 `when: review.rejected`;
   - 用 `build.id` 作为 `parent_node_id` 启动 `fix` 节点。
7. `fix` 节点开始 streaming——时间线上能看到 `↻ build` 角标和从 build 拉过来
   的 SVG 虚线连线。
8. `fix` 完成、其 auto-commit op 完成后,workspace 根的 `mathutils.py` 应当
   除 `add` 外又多了一个你要的函数。下方出现 Verify 卡片。

### 9.3 预期观察到什么

- `build` 完成时,workspace 根**只**有 `mathutils.py` + `.miniclaw2/...`,
  里面只 `def add`。
- `review` 节点的 gate 标签页里的 brief 是 agent **现场写的**(`# How to run`
  里命名了具体 import 命令,不是泛泛而谈)。
- 你写 reject JSON 之后,fix 节点确实启动了,而不是 scenario 直接结束。
- 时间线在 build 和 fix 之间画了一条**虚线**贝塞尔曲线,fix tile 上有
  `↻ build` 角标。
- fix 完成后,`mathutils.py` 同时存在 `def add` 和你 notes 里要求的那个函数。

### 9.4 跑 verify.sh

verify.sh 一次性核对：
- `scenario_step_history` 里 build / review / fix 三步都是 `terminal_state:
  "done"`,且 review 那条带 `decision: "rejected"`;
- fix 节点的 `node.json` 里 `parent_node_id == build.id`;
- fix 的 `provider_session_id`(或 fallback 到 `sdk_session_id`)和 build 一致
  ——证明 resume 边把 provider 会话真的接过来了;
- `mathutils.py` 至少定义两个不同的 `def`(`add` + 一个 fix 加的);
- `git rev-list --count HEAD >= 3`(seed + 至少两次 auto-commit)。

可能的失败：
- `review decision != 'rejected'`：你提交时填的不是 `approved: false`,或者
  gate runner 没把 outcome stamp 上去。
- `fix.parent_node_id != build.id`：resume_from 解析挂了——`history` 里也许
  没找到 build,或者 `start_node` 拒绝了 resume（最常见原因是 build 没拿到
  `provider_session_id`,适配层问题）。
- `fix did not inherit build's provider session`:同上,resume 路径有 bug。
- `mathutils.py only defines [...]`:fix agent 没真的按 notes 改文件,或者它
  把 `add` 也覆盖掉了——后者也算失败。

### 9.5 人工验收清单

- [ ] `build` 节点的 review brief 是 agent 现场写的(`# How to run` 里
      命名了 import `mathutils` 的具体命令)。
- [ ] 你以 `{"approved": false, "notes": "..."}` 形式 reject 了 review，
      review 节点变成深灰色(done)。
- [ ] `fix` 节点在 review 之后真的出现了，tile 上能看到 `↻ build` 角标，
      时间线上有从 build 拉到 fix 的虚线 SVG 连线。
- [ ] `fix` 完成后 `mathutils.py` 同时包含原有的 `add` 和你 notes 里要求的
      新函数(没有把 `add` 删/改坏)。

### 9.6 切换 provider

`Run · codex` 再跑一次。Codex 的 resume 是 thread id 继承（`threadId`），
和 Claude 的 `resume=<sid>` 不是同一条码。如果 Claude 通过但 Codex 端 fix
拿不到 build 的会话(verify 里 `fix did not inherit build's provider
session`)——记下来,适配层信号。

---

## 10. 场景九：`reconnect-replay`（Tier 4）

### 10.1 它在测什么

WebSocket 重连 + replay 路径：agent 在 streaming 时,客户端的 WS 被人为
关掉,`ws.ts` 的重连环会重新打开 WS 并发一个 `replay_request`,带上它最后
看到的 `(node_id, last_seq)`。后端从该 node 的 `events.jsonl` 把缺失的 seq
段回放出来,然后挂回 live tail。整条路径成功的人类可见证据是：transcript
继续往下长、**不**回到开头重放、**不**重复任何已经看过的字。

为了让你能干净地触发这次「掉线」,项目顶 header 会在 `scenario_name ===
"reconnect-replay"` 时额外露出一个 **Simulate WS drop** 按钮。

### 10.2 操作步骤

1. Tests 面板 → `reconnect-replay` → `Run · claude`。
2. 时间线出现 agent 节点开始 streaming;右侧 Chat 面板里 assistant 文本一
   行一行往外吐——每行是 `Fact N: …`(Python 的一句历史)。
3. **等到看到至少三四条 fact 之后(大概 2–5 秒)**,看一下 header 右边——
   `Stop` 按钮旁边会有一个浅色 **Simulate WS drop** 按钮(只有在这个场景下
   才出现)。点它。
4. header 左上角的 `ws` 状态会从 `open` 变成短暂的 `connecting`,然后回到
   `open`。
5. assistant 文本继续往下长,**直接接着第 N+1 条 fact**,不会跳回 `Fact 1:`,
   也不会把已经渲染过的几行再吐一遍。
6. 最后一行应当是 `Fact 10: ... [END]`。节点进入 `done`,下方出现 Verify
   卡片。

### 10.3 预期观察到什么

- Simulate WS drop 按钮**只在该场景下**出现（其它 9 个场景跑的时候 header 里
  没有这个按钮——这是确认 conditional 渲染生效的副产品检查）。
- 点完按钮后 `ws` 指示灯短暂 `connecting` → 回 `open`，整个过程不会久于 1 秒。
- 文本从断点继续往下，没有任何视觉「卷回开头再重放」的迹象,也没有同一条 fact
  连续出现两次。
- 最终 transcript 末尾真的有 `[END]` 标记。

### 10.4 跑 verify.sh

verify.sh 只能从磁盘上观察「JSONL 是不是连续的」——客户端到底有没有真的
看到回放,程序无法直接观察(那是人工验收的领域)。具体核对：
- 该项目的 agent 节点 `state == "done"`;
- 它的 `events.jsonl` 里所有 seq 严格递增、连续(没有空洞、没有重复);
  这是 replay 能否正确工作的**充分必要条件**——后端是从这份 JSONL 回放的;
- 拼出来的 assistant transcript 里包含字面 token `[END]`。

可能的失败：
- `transcript missing [END] marker — agent did not finish`：节点没跑完(可能
  你 Simulate drop 之后 WS 没再连回来,或模型自己截断了)。
- `seq gap or duplicate at index ...`：JSONL 本身有空洞或重复——**这是回归级
  bug**,后端写盘路径有问题,需要立刻报告。
- `agent node state != done`：节点没收尾,可能是被你顺手 Stop 了或 provider 报错。

### 10.5 人工验收清单

- [ ] streaming 进行中(已经看到几条 Fact)的时候你点了 **Simulate WS drop**。
- [ ] 点击后 `ws` 指示灯**短暂**变 `connecting`，然后回 `open`(不是一直停在
      `connecting` 上)。
- [ ] transcript 从断点继续，**没**回到 `Fact 1:` 重放、**没**有任何一条 fact
      连续重复两次。
- [ ] 节点最终进入 `done`(深灰色 tile,不是红色 error),末行带 `[END]`。

### 10.6 切换 provider

`Run · codex` 再跑一次。Codex 是逐 delta 流的,逐 token 颗粒度更细,所以掉
线窗口里看到的「丢字 vs 接上」会比 Claude 端更明显。如果 Claude 端通过但
Codex 端在 replay 之后看到了重复或乱序——记下来,可能是 Codex 适配层在 seq
归一化上的 bug。

---

## 11. 手动流程：ContextSpace bootstrap + bundle snapshot

### 11.1 它在测什么

这不是 Tests 面板里的内置 scenario。它手动验证 ContextSpace v1 主链路：

- 用隔离的 `MINICLAW_HOME` 启动后端；
- 在 UI 里创建默认 ContextSpace；
- session 写入 project binding 和 active planspace；
- node launch 时同时注入 project `CONTEXT.md` 和 planspace `STATUS.md` /
  `PLAN.md`；
- Node detail 里能追溯 context bundle sources。

### 11.2 启动隔离测试环境

先准备一个只用于这次测试的 home 和 workspace：

```bash
rm -rf /private/tmp/miniclaw2-contextspace-test
mkdir -p /private/tmp/miniclaw2-contextspace-test/workspace

cat >/private/tmp/miniclaw2-contextspace-test/workspace/CONTEXT.md <<'EOF'
# ContextSpace Manual Test Workspace

This project exists only for MiniClaw2 ContextSpace testing.

Agents should mention the phrase `contextspace-manual-test` when asked to
summarize the project context.
EOF
```

启动后端，注意这里的特殊 `MINICLAW_HOME`：

```bash
cd backend
MINICLAW_HOME=/private/tmp/miniclaw2-contextspace-test/home \
  python -m miniclaw2 --host 127.0.0.1 --port 8000 --log-level info
```

另开一个 shell 启动前端：

```bash
cd frontend
npm run dev
```

打开：

```text
http://localhost:5173/
```

### 11.3 创建测试 session

可以在 UI 里新建项目：

- name：`ContextSpace Manual Test`
- cwd：`/private/tmp/miniclaw2-contextspace-test/workspace`
- provider：`codex` 或 `claude`

也可以直接用 API 创建：

```bash
curl -s -X POST http://127.0.0.1:8000/sessions \
  -H 'content-type: application/json' \
  -d '{"cwd":"/private/tmp/miniclaw2-contextspace-test/workspace","provider":"codex","name":"ContextSpace Manual Test"}'
```

### 11.4 在 UI 里 bootstrap ContextSpace

1. 选择 `ContextSpace Manual Test`。
2. 打开 `ContextSpace` 面板。
3. 先确认：
   - root 是 `/private/tmp/miniclaw2-contextspace-test/home/contextspace`；
   - `Root` 是 `missing`；
   - `Resolved binding` 是 `none`。
4. 点击 `Create`。
5. Title 填：`Manual ContextSpace Track`。
6. 创建后确认：
   - `Root` 变成 `present`；
   - resolved binding 是 `project.manual-contextspace-track`；
   - active planspace 是 `planspaces.manual-contextspace-track`。

### 11.5 启动一个节点验证注入

在聊天框发送：

```text
Summarize the loaded project/contextspace context. Mention whether you saw the phrase contextspace-manual-test.
```

如果 provider 请求写 `.miniclaw2/outputs/.../result.md` 的权限，点 Allow。
这是正常现象。

### 11.6 预期观察到什么

ContextSpace 目录应当出现这些文件：

```text
/private/tmp/miniclaw2-contextspace-test/home/contextspace/contextspace.yaml
/private/tmp/miniclaw2-contextspace-test/home/contextspace/bindings/projects/project.manual-contextspace-track.yaml
/private/tmp/miniclaw2-contextspace-test/home/contextspace/plugs/planspaces/manual-contextspace-track/manifest.yaml
/private/tmp/miniclaw2-contextspace-test/home/contextspace/plugs/planspaces/manual-contextspace-track/STATUS.md
/private/tmp/miniclaw2-contextspace-test/home/contextspace/plugs/planspaces/manual-contextspace-track/PLAN.md
/private/tmp/miniclaw2-contextspace-test/home/contextspace/plugs/planspaces/manual-contextspace-track/SKILLS.md
```

节点完成后，在 Node detail 的 Context 区域应当看到：

- `Context bundle` 有 id；
- `Binding` 是 `project.manual-contextspace-track`；
- `Active planspace` 是 `planspaces.manual-contextspace-track`；
- sources 里有三项：
  - workspace 的 `CONTEXT.md`，`injection = system`；
  - planspace 的 `STATUS.md`，`injection = turn`；
  - planspace 的 `PLAN.md`，`injection = turn`。

assistant 的回复或 summary artifact 应当明确提到：

```text
contextspace-manual-test
```

### 11.7 验证 memory delta 写回闭环

在同一个 session 里再启动一个节点，发送：

```text
Create a ContextSpace memory delta artifact for this completed node. Add a STATUS.md append_observation with policy auto whose text contains contextspace-memory-delta-manual-ok. Also include one PLAN.md propose_patch update with policy proposed whose patch contains do-not-apply-plan-proposal. Do not edit ContextSpace files directly.
```

节点进入 `done` 后检查：

- workspace 里有这个 project-local artifact：

  ```text
  /private/tmp/miniclaw2-contextspace-test/workspace/.miniclaw2/outputs/<node-id>/memory-delta.json
  ```

- ContextSpace 里有后端复制进去的 inbox 文件：

  ```text
  /private/tmp/miniclaw2-contextspace-test/home/contextspace/plugs/planspaces/manual-contextspace-track/inbox/<node-id>.memory-delta.json
  ```

- planspace 的 `STATUS.md` 包含：

  ```text
  contextspace-memory-delta-manual-ok
  node <node-id>
  acceptance_state: unreviewed
  ```

- planspace 的 `PLAN.md` **不**包含：

  ```text
  do-not-apply-plan-proposal
  ```

- planspace 的 `events.jsonl` 包含 `memory_delta_applied`，并且事件里能看到
  一个 proposal 被记录；
- Node detail -> Settings -> Memory delta 显示 `applied 1`、`proposed 1`，
  source 是 `project_artifact`。

### 11.8 判定结果

这条手动流程通过 = 同时满足：

- UI bootstrap 成功；
- session 绑定了 `project.manual-contextspace-track`；
- active planspace 是 `planspaces.manual-contextspace-track`；
- node 正常进入 `done`；
- context bundle sources 包含 `CONTEXT.md`、`STATUS.md`、`PLAN.md`；
- assistant 看到并提到了 `contextspace-manual-test`；
- 第二个 node 产生 project-local `memory-delta.json`；
- `STATUS.md` 自动追加了 observation；
- `PLAN.md` proposal 被记录但没有自动应用；
- Node detail 的 Settings 能看到 memory delta 应用结果；
- 后端日志没有 ContextSpace 异常。

---

## 12. 一些通用注意事项

### 12.1 verify.sh 在哪里跑、看到了什么

`verify.sh` 由后端 `/sessions/{sid}/verify` 端点同步触发：
- 工作目录是 workspace 临时根（`miniclaw2-tmp-xxxx`）；
- 后端把 `MINICLAW_PROJECT_ID` 和 `MINICLAW_HOME` 注入到 env，让脚本能从磁盘
  事件日志里读 transcript 和工具调用；
- 60 秒超时；超时时 `exit_code=124` 且 `timed_out=true`。

### 12.2 跑完之后的清理

每个场景跑完一遍，后端会留着 session 和它的 workspace。如果你想清理：

```bash
curl -X DELETE http://127.0.0.1:8000/sessions/<sid>
```

会同时清理临时 workspace 和 `$MINICLAW_HOME/projects/<sid>/`。

如果你刚才切了 provider 又切了别的、session 没显式删，临时目录会留下来——
不会影响下一次运行，但磁盘会慢慢攒。批量清理：

```bash
rm -rf /var/folders/*/T/miniclaw2-tmp-*    # macOS
rm -rf /tmp/miniclaw2-tmp-*                # Linux
```

### 12.3 还没覆盖到的能力

本指南**已经**覆盖了 Tier 1/2/3/4 的大部分主路径——checkpoint gate
（§9 `resume-fix-after-reject` 的 review 步）、resume 边 + provider 会话继承
（§9）、CONTEXT.md 注入（§8）、WS 重连 replay（§10）都有专门的章节。
下面这些路径目前仍**没有**本指南级别的中文走查：

- 内联 `ask_user` gate（剩余的一种内联 gate；permission 和 plan_approval 已经
  在 §5、§6 覆盖了）。`ask_user` 还没有专门的内置 scenario。
- Tier 3 旗舰 `gui-calculator`（构建 PySide6/Qt 计算器 → 被动 review gate →
  auto-commit 改写 `commit_after`）。**它已经在仪表盘里可以跑**，运行方式
  同其它场景：点 `Run · claude` / `Run · codex`，时间线上会出现 build
  agent → 自动 commit op → 被动 review gate；review brief 由 build agent
  现场写，你在 gate tab 里以 write-json 形式提交评审意见。GUI 行为的人工
  验收清单见 `backend/miniclaw2/scenarios/bundled/gui-calculator/acceptance.md`
  （计算 1+2=3、9÷0 不抛 Python traceback、C 清屏、关窗能正常退出）。
  它是验证 auto-commit op 是否正常的主路径——回归会同时打破 verify 和
  acceptance。

如果你希望本指南也为 `gui-calculator` 写一节详尽中文走查，欢迎在 repo 里提
issue 催更。

### 12.4 失败时该怎么办

- **provider 鉴权失败**：节点会迅速进 `error`，事件流里出现 error 事件。检查
  `claude` / `codex` CLI 是否能跑通。
- **WS 一直 `connecting`**：检查后端有没有起来、端口对不对、有没有被
  Cloudflare/代理拦。
- **verify.sh 找不到 events.jsonl**：检查 `MINICLAW_HOME` 是否一致——如果
  后端在不同 shell 下用了不同的 `MINICLAW_HOME`，verify 看到的就是空目录。
- **gate 标签页没自动弹出**：检查时间线上对应节点是不是被选中了。如果你停在
  别的节点上，应当能看到一条琥珀色提示条"Node X is awaiting your response"——
  点一下就跳过去。
- **模型答得"差不多对但不严格"**：这是手工测试的核心价值——脚本能挑出来，
  人眼也能挑出来。把这一轮判否、记下 provider 名和现象，往后再跑就行。

### 12.5 一句话总结判定规则

- ✅ **该场景该 provider 通过** = verify.sh `exit 0` **并且** 人工验收清单全勾。
- ✅ **该场景通过** = Claude 和 Codex 各自都满足上一条。

不存在"差不多通过"或"verify 过了就行"。这是设计上的硬性约定。
