from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .errors import MigrationError
if TYPE_CHECKING:
    from .sdk import Migration

DIRECTORY = Path(__file__).parent
BASELINE = 14
WINDOW = 3
PROTOCOL = 1
FAMILY = "miniclaw2-store"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_manifest() -> dict[str, Any]:
    return json.loads((DIRECTORY / "manifest.json").read_text(encoding="utf-8"))


def generate(*, prune: bool = False) -> dict[str, Any]:
    previous = read_manifest() if (DIRECTORY / "manifest.json").exists() else {}
    entries = []
    contracts = previous.get("contracts", {str(BASELINE): digest(b"miniclaw2/store/v14")})
    for path in sorted((DIRECTORY / "steps").glob("v*.py")):
        migration: Migration = importlib.import_module(f"{__package__}.steps.{path.stem}").MIGRATION
        if migration.source + 1 != migration.target:
            raise ValueError(f"{path.name} 必须描述相邻版本")
        entry = {"source": migration.source, "target": migration.target, "module": path.stem, "sha256": digest(path.read_bytes())}
        old = next((item for item in previous.get("steps", []) if item["target"] == migration.target), None)
        if old is not None and old != entry:
            raise ValueError(f"已发布的迁移不可修改：{path.name}")
        contracts[str(migration.target)] = digest(migration.contract.encode())
        entries.append(entry)
    target = max((entry["target"] for entry in entries), default=BASELINE)
    minimum = max(BASELINE, target - WINDOW)
    retained = [entry for entry in entries if entry["source"] >= minimum]
    if [entry["source"] for entry in retained] != list(range(minimum, target)):
        raise ValueError("迁移链断裂或目标版本重复")
    retired = [entry for entry in entries if entry["source"] < minimum]
    if retired and not prune:
        raise ValueError("存在已退役脚本，请运行 migrations release --prune")
    for entry in retired:
        (DIRECTORY / "steps" / f'{entry["module"]}.py').unlink()
        fixture = DIRECTORY.parents[1] / "tests" / "migrations" / f'test_{entry["module"]}.py'
        fixture.unlink(missing_ok=True)
    manifest = {"family": FAMILY, "protocol": PROTOCOL, "target": target, "minimum": minimum,
                "contracts": {str(version): contracts[str(version)] for version in range(minimum, target + 1)}, "steps": retained}
    (DIRECTORY / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def check_manifest() -> None:
    manifest = read_manifest()
    expected = {entry["module"] + ".py" for entry in manifest["steps"]}
    actual = {path.name for path in (DIRECTORY / "steps").glob("v*.py")}
    if expected != actual or manifest["minimum"] != max(BASELINE, manifest["target"] - WINDOW):
        raise MigrationError("schema_conflict", "发行清单与三步支持窗口不一致")
    if [entry["source"] for entry in manifest["steps"]] != list(range(manifest["minimum"], manifest["target"])):
        raise MigrationError("schema_conflict", "发行迁移链不连续")
    for entry in manifest["steps"]:
        if entry["target"] != entry["source"] + 1 or digest((DIRECTORY / "steps" / (entry["module"] + ".py")).read_bytes()) != entry["sha256"]:
            raise MigrationError("schema_conflict", "发行迁移脚本摘要不一致")


def steps(source: int) -> list[Migration]:
    check_manifest()
    return [importlib.import_module(f'{__package__}.steps.{entry["module"]}').MIGRATION
            for entry in read_manifest()["steps"] if entry["source"] >= source]


def marker(version: int | None = None) -> dict[str, Any]:
    manifest = read_manifest()
    version = manifest["target"] if version is None else version
    return {"schema": FAMILY, "schema_version": version, "protocol": PROTOCOL,
            "contract": manifest["contracts"][str(version)]}


def version_of(payload: Any, path: Path) -> int:
    if not isinstance(payload, dict) or type(payload.get("schema_version")) is not int:
        raise MigrationError("migration_failed", "格式版本必须是整数，不能缺失或为布尔值", path)
    version = payload["schema_version"]
    manifest = read_manifest()
    if version < manifest["minimum"]:
        raise MigrationError("schema_too_old", f'数据 v{version} 低于最低 v{manifest["minimum"]}；请保留原数据，通过覆盖该版本的中间版本升级', path)
    if version > manifest["target"]:
        raise MigrationError("schema_too_new", f'数据 v{version} 新于程序 v{manifest["target"]}；请先更新程序', path)
    if version == BASELINE and payload.get("schema") == "node-revision-v9" and "contract" not in payload:
        return version
    expected = marker(version)
    if any(payload.get(key) != value for key, value in expected.items()):
        raise MigrationError("schema_conflict", "同代格式契约或引擎协议不一致", path)
    return version


_manifest_path = DIRECTORY / "manifest.json"
CURRENT_VERSION = read_manifest()["target"] if _manifest_path.exists() else BASELINE
MINIMUM_VERSION = read_manifest()["minimum"] if _manifest_path.exists() else BASELINE
