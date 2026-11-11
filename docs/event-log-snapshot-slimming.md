# 事件日志不再持久化节点 prompt 快照

## 背景

元数据同步时推送量过大（一次积压约 52 MB 打包数据，其中约 47 MB 来自
`projects/*/hosts/*/nodes/*/events.jsonl`）。统计本机全部 `events.jsonl`（约 1253 MB）：

- `node_updated` 事件占 32%，其中 `node.launch_instructions_snapshot` 占 16%，
  `node.system_context_snapshot` 占 8%；`node_started` / `turn_done` 中同样字段另占约 3%。
- 这两个字段每条约 14 KB，在同一节点的事件间内容基本不变，却被逐条重复写入。

这两个字段从不经由事件被消费：

- 实时推送：`NodeRunner` 的 `on_event` 是 `ProjectRuntime.broadcast`
  （`backend/miniclaw2/registry.py`），它先调用 `node_event_projection` 剔除
  `NODE_DETAIL_FIELDS`。
- 重放：`registry.py` 中读取 `store.replay_events` 后同样经 `node_event_projection` 返回。
- 前端读取快照（`AgentPanel.tsx`、`InspectDrawer.tsx`）走节点详情接口，数据来自
  `node.json`，与事件日志无关。

完整快照仍保存在 `node.json`（`Store.update_node`），不受影响。

## 修改

在事件**写盘**时剔除 `NODE_DETAIL_FIELDS`，只改持久化，不改实时广播内容。

1. `backend/miniclaw2/node_projection.py` 新增一个只删除快照字段的函数，例如：

   ```python
   def node_event_persisted(event: dict[str, Any]) -> dict[str, Any]:
       if event.get("type") in NODE_EVENTS and isinstance(event.get("node"), dict):
           node = {k: v for k, v in event["node"].items() if k not in NODE_DETAIL_FIELDS}
           return {**event, "node": node}
       return event
   ```

   不要直接复用 `node_event_projection`：它还会截断 `prompt`、补
   `prompt_argument_names` 等，属于展示投影，不应写入持久数据。

2. `backend/miniclaw2/store.py` 的 `Store.append_event` 在 `json.dumps` 前对 `event`
   调用该函数。`runner.py` 中 `_emit` 传给 `on_event` 的 `data` 保持原样（不要原地修改
   传入的 dict，函数需返回新 dict）。

## 不做的事

- 不升级 `EVENT_SCHEMA_VERSION`，不加迁移：字段缺失与旧数据中字段存在都能被现有读取路径
  正确处理（投影本来就会删掉它们），`migrations/validation.py` 只校验事件外层结构。
- 不改写已有 `events.jsonl`：历史数据已进入 git 历史，改写只会再产生一份大 diff，收益为负。
- 不改 `node.json` 中的快照字段，不改前端。

## 测试

在 `backend/tests/` 中补充（可放在 `test_node_projection.py` 或 `test_replay.py`）：

- 运行/模拟一个节点产生 `node_updated`、`node_started`、`turn_done` 事件后，读取
  `events.jsonl` 原始行，断言 `event.node` 中不含 `launch_instructions_snapshot`、
  `system_context_snapshot`，其余字段（如 `prompt`、`state`、`usage`）完整未截断。
- 同一场景下 `node.json` 仍保留两个快照字段的完整内容。
- 实时广播收到的事件与修改前一致（经投影后同样不含快照字段）。
- 旧格式事件（`node` 中含快照字段）经 `replay_events` + 投影后结果不变。

运行全部后端测试：`python -m pytest backend/tests -q`（需要 `pip install -e 'backend[test]'`）。
已知 `test_remote_transport.py::test_control_path_fits_sun_path_under_long_private_tmpdir`
在 Linux 上本就失败（依赖 macOS 路径），与本修改无关。

## 预期效果

新产生的事件日志体积减少约 25%，同步推送量随之按比例下降。
