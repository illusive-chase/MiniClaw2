"""Validation, publication, and durable paths for rendered artifacts."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from .domain import ArtifactRef, Node, Project
from .materialize import ARTIFACTS_DIRNAME
from .store import Store


MAX_ARTIFACTS_PER_NODE = 16
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024
MAX_ARTIFACTS_TOTAL_BYTES = 8 * 1024 * 1024
INLINE_TEXT_CAP = 512 * 1024
ALLOWED_ARTIFACT_SUFFIXES = frozenset({".md", ".json", ".html"})


def workspace_artifacts_dir(project: Project, node_id: str) -> Path:
    return Path(project.root_path) / ARTIFACTS_DIRNAME / node_id


def stored_artifacts_dir(store: Store, project_id: str, node_id: str) -> Path:
    return store.node_dir(project_id, node_id) / "artifacts"


def stored_artifact_path(
    store: Store,
    project_id: str,
    node_id: str,
    name: str,
) -> Path:
    return stored_artifacts_dir(store, project_id, node_id) / name


def publish_artifacts(
    project: Project,
    node: Node,
    declared: list[str],
    store: Store,
) -> list[ArtifactRef]:
    """Validate declarations and replace the node's durable artifact copy."""
    source_root = workspace_artifacts_dir(project, node.id)
    destination = stored_artifacts_dir(store, project.id, node.id)
    staging = destination.with_name("artifacts.tmp")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)

    refs: list[ArtifactRef] = []
    total_bytes = 0
    seen_names: set[str] = set()
    for index, name in enumerate(declared):
        if name in seen_names:
            continue
        seen_names.add(name)
        if index >= MAX_ARTIFACTS_PER_NODE:
            refs.append(_dropped(name, f"exceeds {MAX_ARTIFACTS_PER_NODE} artifact limit"))
            continue
        invalid_reason = _invalid_name_reason(name)
        if invalid_reason is not None:
            refs.append(_dropped(name, invalid_reason))
            continue

        source = source_root / name
        try:
            source_resolved = source.resolve(strict=True)
            root_resolved = source_root.resolve(strict=False)
        except (OSError, RuntimeError):
            refs.append(_dropped(name, "file does not exist"))
            continue
        if source_resolved.parent != root_resolved:
            refs.append(_dropped(name, "resolves outside the node outputs directory"))
            continue
        if not source_resolved.is_file():
            refs.append(_dropped(name, "not a regular file"))
            continue

        try:
            source_stat = source_resolved.stat()
        except OSError as exc:
            refs.append(_dropped(name, f"cannot stat file: {exc}"))
            continue
        if source_stat.st_size > MAX_ARTIFACT_BYTES:
            refs.append(
                _dropped(
                    name,
                    f"exceeds {MAX_ARTIFACT_BYTES // (1024 * 1024)} MiB cap",
                    size=source_stat.st_size,
                    mtime=source_stat.st_mtime,
                )
            )
            continue
        if total_bytes + source_stat.st_size > MAX_ARTIFACTS_TOTAL_BYTES:
            refs.append(
                _dropped(
                    name,
                    f"exceeds {MAX_ARTIFACTS_TOTAL_BYTES // (1024 * 1024)} MiB node budget",
                    size=source_stat.st_size,
                    mtime=source_stat.st_mtime,
                )
            )
            continue

        target = staging / name
        try:
            shutil.copy2(source_resolved, target)
            target_stat = target.stat()
            if target_stat.st_size > MAX_ARTIFACT_BYTES:
                target.unlink(missing_ok=True)
                refs.append(
                    _dropped(
                        name,
                        f"exceeds {MAX_ARTIFACT_BYTES // (1024 * 1024)} MiB cap",
                        size=target_stat.st_size,
                        mtime=target_stat.st_mtime,
                    )
                )
                continue
            if total_bytes + target_stat.st_size > MAX_ARTIFACTS_TOTAL_BYTES:
                target.unlink(missing_ok=True)
                refs.append(
                    _dropped(
                        name,
                        f"exceeds {MAX_ARTIFACTS_TOTAL_BYTES // (1024 * 1024)} MiB node budget",
                        size=target_stat.st_size,
                        mtime=target_stat.st_mtime,
                    )
                )
                continue
            digest = _sha256(target)
        except OSError as exc:
            target.unlink(missing_ok=True)
            refs.append(_dropped(name, f"cannot copy file: {exc}"))
            continue

        total_bytes += target_stat.st_size
        refs.append(
            ArtifactRef(
                name=name,
                bytes=target_stat.st_size,
                mtime=target_stat.st_mtime,
                sha256=digest,
                status="published",
            )
        )

    shutil.rmtree(destination, ignore_errors=True)
    staging.replace(destination)
    node.artifacts = refs
    return refs


def clear_published_artifacts(project: Project, node: Node, store: Store) -> None:
    publish_artifacts(project, node, [], store)


def _invalid_name_reason(name: str) -> str | None:
    if (
        not name
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or "\x00" in name
    ):
        return "must be a bare filename"
    if Path(name).name != name:
        return "must be a bare filename"
    if Path(name).suffix not in ALLOWED_ARTIFACT_SUFFIXES:
        return "suffix must be .md, .json, or .html"
    return None


def _dropped(
    name: str,
    reason: str,
    *,
    size: int = 0,
    mtime: float = 0.0,
) -> ArtifactRef:
    return ArtifactRef(
        name=name,
        bytes=size,
        mtime=mtime,
        sha256="",
        status="dropped",
        reason=reason,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(128 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
