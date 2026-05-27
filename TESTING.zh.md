# MiniClaw2 手动测试指南（Tier 1）

本文档面向用户，介绍如何在仪表盘里逐个运行 Tier 1 的三个内置测试场景，并手工验证它们是否真的工作。

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

浏览器打开前端地址，你会看到顶部右侧有 `Claude/Codex` 选择器、`+ Gate` 按钮、
以及 `Chat | Tests` 两个标签。

### 0.3 关于 provider 的说明

**每个场景都必须分别用 Claude 和 Codex 各跑一次，两个 provider 都通过才算这个
场景通过。** 这是有意为之的——provider 之间的差异本身就是有价值的信号
（适配层有问题 vs. provider 自身能力问题，都需要暴露）。

---

## 1. 打开测试面板

1. 在顶部 header 点击 **Tests** 标签。
2. 你应该看到三行场景：`bash-uname`、`hello-text`、`write-readme`，每行右侧有
   `Run · claude` 和 `Run · codex` 两个按钮。
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

## 5. 一些通用注意事项

### 5.1 verify.sh 在哪里跑、看到了什么

`verify.sh` 由后端 `/sessions/{sid}/verify` 端点同步触发：
- 工作目录是 workspace 临时根（`miniclaw2-tmp-xxxx`）；
- 后端把 `MINICLAW_PROJECT_ID` 和 `MINICLAW_HOME` 注入到 env，让脚本能从磁盘
  事件日志里读 transcript 和工具调用；
- 60 秒超时；超时时 `exit_code=124` 且 `timed_out=true`。

### 5.2 跑完之后的清理

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

### 5.3 还没覆盖到的能力

下面这些路径目前**没有**对应的内置场景，需要等 Tier 2/3 落地：

- 内联 gate（permission / ask-user / plan-approval）。
- checkpoint gate（contract + write-json 评审）。
- auto-commit op、`commit_after` 改写。
- resume 边、跨节点继续会话。
- CONTEXT.md 注入。
- WS 重连 replay。

特别提醒：**想验证 auto-commit op 是否正常**，应当跑 Tier 3 的 `gui-calculator`
场景（它把 auto-commit 放在主路径上，回归会同时打破 verify 和 acceptance）。
目前 `gui-calculator` 还没实现，等它落地后会在这里加一节。

### 5.4 失败时该怎么办

- **provider 鉴权失败**：节点会迅速进 `error`，事件流里出现 error 事件。检查
  `claude` / `codex` CLI 是否能跑通。
- **WS 一直 `connecting`**：检查后端有没有起来、端口对不对、有没有被
  Cloudflare/代理拦。
- **verify.sh 找不到 events.jsonl**：检查 `MINICLAW_HOME` 是否一致——如果
  后端在不同 shell 下用了不同的 `MINICLAW_HOME`，verify 看到的就是空目录。
- **模型答得"差不多对但不严格"**：这就是 Tier 1 的核心价值——脚本能挑出来，
  人眼也能挑出来。把这一轮判否、记下 provider 名和现象，往后再跑就行。

### 5.5 一句话总结判定规则

- ✅ **该场景该 provider 通过** = verify.sh `exit 0` **并且** 人工验收清单全勾。
- ✅ **该场景通过** = Claude 和 Codex 各自都满足上一条。

不存在"差不多通过"或"verify 过了就行"。这是设计上的硬性约定。
