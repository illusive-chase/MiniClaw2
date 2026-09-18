"""Local control tools for agents whose native filesystem tools run remotely."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .artifacts import (
    MAX_ARTIFACT_BYTES, MAX_ARTIFACTS_PER_NODE, MAX_ARTIFACTS_TOTAL_BYTES,
    _invalid_name_reason, workspace_artifacts_dir,
)
from .domain import COLD_START_AGENT_OP_KIND, Category, Node, Project
from .materialize import runner_lane_root
from .preview import ExecutedPreview, parse_preview, validate_preview_for_node


_IDENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class RemoteGraphTools:
    def __init__(self, project: Project, node: Node) -> None:
        self.node = node
        canonical_project = project.model_copy(update={"root_path": str(Path(project.root_path).resolve())})
        self.lane = runner_lane_root(canonical_project, node.id, node.planspace_id or "")
        self.outputs = workspace_artifacts_dir(canonical_project, node.id)

    def definitions(self) -> list[dict[str, Any]]:
        specs = [
            ("lane_list", "列出本次执行可读取的本机 lane 文件。", {}, []),
            ("lane_read", "分段读取 lane 文件；path 使用 lane_list 返回的相对路径。", {
                "path": {"type": "string"}, "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 65536},
            }, ["path"]),
            ("publish_preview", "提交自己的完整预览 JSON；planning/review 也可提交同 lane 虚拟预览，最终由 reap 统一校验。", {
                "preview": {"type": "object"},
            }, ["preview"]),
            ("publish_artifact", "在本机发布文本产物。长文件分段传入，后续段使用 append=true。", {
                "name": {"type": "string"}, "content": {"type": "string"},
                "append": {"type": "boolean"},
            }, ["name", "content"]),
        ]
        if self.node.agent_op_kind == COLD_START_AGENT_OP_KIND:
            specs = [spec for spec in specs if spec[0] == "publish_artifact"]
        return [{
            "type": "function", "name": name, "description": description,
            "inputSchema": {"type": "object", "additionalProperties": False,
                            "properties": properties, "required": required},
        } for name, description, properties, required in specs]

    def call(self, name: str, arguments: Any) -> dict[str, Any]:
        try:
            if not isinstance(arguments, dict):
                raise ValueError("工具参数必须为对象")
            definition = next((d for d in self.definitions() if d["name"] == name), None)
            if definition is None:
                raise ValueError("未知控制工具")
            schema = definition["inputSchema"]
            if set(arguments) - set(schema["properties"]) or not set(schema["required"]) <= set(arguments):
                raise ValueError("工具参数缺失或包含未知字段")
            result = self._call(name, arguments)
            return {"success": True, "contentItems": [{"type": "inputText", "text": json.dumps(result, ensure_ascii=False)}]}
        except (ValueError, OSError, RuntimeError, TypeError) as exc:
            return {"success": False, "contentItems": [{"type": "inputText", "text": str(exc)}]}

    @staticmethod
    def _path(root: Path, relative: str) -> Path:
        if not isinstance(relative, str) or "\x00" in relative or "\\" in relative:
            raise ValueError("控制文件路径无效")
        path = Path(relative)
        if path.is_absolute() or any(p in {"..", "."} for p in path.parts) or not path.parts:
            raise ValueError("控制文件路径越界")
        target = root / path
        # Do not follow links, even links currently resolving within the root.
        for ancestor in [root, *root.parents, target, *target.parents]:
            if ancestor.is_symlink():
                raise ValueError("控制文件路径不能经过符号链接")
        if not target.resolve().is_relative_to(root.resolve()):
            raise ValueError("控制文件路径越界")
        return target

    def _call(self, name: str, arguments: dict[str, Any]) -> Any:
        if name == "lane_list":
            return {"files": [str(p.relative_to(self.lane)) for p in sorted(self.lane.rglob("*"))
                              if p.is_file() and not p.is_symlink()]}
        if name == "lane_read":
            path = self._path(self.lane, arguments["path"])
            offset, limit = arguments.get("offset", 0), arguments.get("limit", 32768)
            if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 65536:
                raise ValueError("读取范围无效")
            with path.open(encoding="utf-8") as handle:
                # Character offsets keep multibyte text intact across chunks.
                remaining = offset
                while remaining:
                    skipped = handle.read(min(remaining, 65536))
                    if not skipped:
                        break
                    remaining -= len(skipped)
                text = handle.read(limit)
                more = bool(handle.read(1))
            return {"text": text, "next_offset": offset + len(text), "more": more}
        if name == "publish_preview":
            payload = arguments["preview"]
            if not isinstance(payload, dict):
                raise ValueError("preview 必须为对象")
            text = json.dumps(payload, ensure_ascii=False, indent=2)
            if len(text.encode()) > MAX_ARTIFACT_BYTES:
                raise ValueError("预览超过大小限制")
            preview = parse_preview(text)
            if not _IDENT.fullmatch(preview.id):
                raise ValueError("预览标识无效")
            if preview.lane != (self.node.planspace_id or ""):
                raise ValueError("不能写入其他 lane")
            if preview.id == self.node.id:
                if not isinstance(preview, ExecutedPreview):
                    raise ValueError("自己的预览必须是已执行预览")
                issues = validate_preview_for_node(preview, self.node)
                if issues:
                    raise ValueError("; ".join(issues))
            else:
                if self.node.category not in {Category.PLANNING, Category.REVIEW}:
                    raise ValueError("regular 只能发布自己的预览")
                if isinstance(preview, ExecutedPreview):
                    raise ValueError("不能修改其他已执行预览")
                existing = self._path(self.lane, f"nodes/{preview.id}/preview.json")
                if existing.exists() and isinstance(parse_preview(existing.read_text()), ExecutedPreview):
                    raise ValueError("不能改写已执行记录")
            target = self._path(self.lane, f"nodes/{preview.id}/preview.json")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text + "\n", encoding="utf-8")
            return {"id": preview.id, "received": True}
        if name == "publish_artifact":
            filename, content = arguments["name"], arguments["content"]
            if not isinstance(filename, str) or not isinstance(content, str):
                raise ValueError("产物名称与内容必须为字符串")
            invalid = _invalid_name_reason(filename)
            if invalid:
                raise ValueError(invalid)
            append = arguments.get("append", False)
            if type(append) is not bool:
                raise ValueError("append 必须为布尔值")
            target = self._path(self.outputs, filename)
            previous = target.read_bytes() if append and target.exists() else b""
            data = previous + content.encode("utf-8")
            files = [p for p in self.outputs.glob("*") if p != target]
            if len(files) >= MAX_ARTIFACTS_PER_NODE or len(data) > MAX_ARTIFACT_BYTES:
                raise ValueError("产物超出数量或单文件大小限制")
            if sum(p.stat().st_size for p in files) + len(data) > MAX_ARTIFACTS_TOTAL_BYTES:
                raise ValueError("产物超出本次执行总大小限制")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            return {"name": filename, "bytes": len(data)}
        raise ValueError("未知控制工具")


def remote_launch_block(node: Node) -> str:
    if node.agent_op_kind == COLD_START_AGENT_OP_KIND:
        return (
            "# 远端执行位置\n\n内置 shell/file 工具操作远端权威工作树。"
            "如需交付文件，使用 publish_artifact 在本机发布文本产物；"
            "仅支持 .md/.json/.html/.svg，单文件 2 MiB、最多 16 个、总计 8 MiB。"
            f"本次产物模式：{node.artifact_mode.value}。{node.artifact_spec or ''}"
        )
    example: dict[str, Any] = {
        "id": node.id, "kind": "agent", "category": node.category.value,
        "state": "done", "ran_at": "<ISO 8601 UTC>", "lane": node.planspace_id or "",
        "motivation": "<执行动机>", "summary": "<结果>",
        "next_implications": "<后续影响>", "artifacts": [],
    }
    if node.subtype:
        example["subtype"] = node.subtype.value
    return (
        "# MiniClaw2 远端执行契约\n\n"
        "内置 shell/file 工具仅操作远端权威工作树。lane、预览、产物留在本机，"
        "不能用远端文件工具访问，也不能尝试连接本机 backend。\n"
        "使用 lane_list 和 lane_read 读取依赖预览及其引用的 transcript/artifacts。"
        f"依赖标识：{json.dumps(node.scheduled_deps)}。\n"
        "结束前必须调用 publish_preview，传入完整 preview 对象：\n"
        f"```json\n{json.dumps(example, ensure_ascii=False, indent=2)}\n```\n"
        + ("本次为 regular，只能发布自己的预览，不能改写计划。\n" if node.category is Category.REGULAR else
           "可通过 publish_preview 新建或修改同 lane 的 virtual 预览，不能改写已执行记录；"
           "使用 state=virtual、id、kind=agent、category、lane、proposed_by、motivation、"
           "prompt_draft、scheduled_deps；review 还需 subtype 与 brief。"
           "删除计划应填写 obsolete_reason，不删除文件。最终由框架校验依赖和环路。\n")
        + (f"审查要求：{node.brief.model_dump_json()}\n" if node.brief else "")
        + (f"必须读取 nodes/{node.id}/human-review.md 中的人类意见。\n" if node.subtype == "human_interact_review" else "")
        + f"产物模式：{node.artifact_mode.value}。{node.artifact_spec or ''}\n"
        "仅在用户要求时发布产物；显式非 default 模式必须交付对应产物。"
        "使用 publish_artifact，文件后缀仅 .md/.json/.html/.svg；"
        "最多 16 个，单文件 2 MiB、总计 8 MiB；长文件分段 append。"
        "HTML 必须自包含。最后在自己的 preview.artifacts 列出文件名。\n"
    )
