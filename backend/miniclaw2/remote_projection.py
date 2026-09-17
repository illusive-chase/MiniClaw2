"""One-way, tracked-file projections of authoritative remote worktrees."""

from __future__ import annotations

import hashlib
import io
import os
import posixpath
import shutil
import tarfile
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .domain import RemoteProjectBinding
from .remote_transport import RemoteTransportError, SSHProjectTransport


_PROJECTION_LOCKS_GUARD = threading.Lock()
_PROJECTION_LOCKS: dict[str, threading.Lock] = {}


@dataclass(frozen=True, slots=True)
class ProjectionSyncResult:
    binding: RemoteProjectBinding
    distorted_paths: tuple[str, ...]
    file_count: int


def sync_remote_projection(
    binding: RemoteProjectBinding,
    *,
    remote_root: str,
    transport: SSHProjectTransport,
) -> ProjectionSyncResult:
    """Replace the local projection from a validated remote tar snapshot."""
    # Keep the lexical path so a projection root replaced with a symlink is
    # visible to the checks below instead of being resolved into its target.
    configured = Path(binding.projection_path)
    projection = configured.parent.resolve(strict=False) / configured.name
    with _PROJECTION_LOCKS_GUARD:
        lock = _PROJECTION_LOCKS.setdefault(str(projection), threading.Lock())
    with lock:
        return _sync_remote_projection(
            binding,
            remote_root=remote_root,
            transport=transport,
            projection=projection,
        )


def _sync_remote_projection(
    binding: RemoteProjectBinding,
    *,
    remote_root: str,
    transport: SSHProjectTransport,
    projection: Path,
) -> ProjectionSyncResult:
    backup = projection.with_name(f".{projection.name}.projection-backup")
    if projection.is_symlink():
        raise RemoteTransportError("本地投影根目录不能是符号链接")
    if backup.is_symlink():
        raise RemoteTransportError("本地投影备份目录不能是符号链接")
    projection.parent.mkdir(parents=True, exist_ok=True)
    if backup.exists() and not projection.exists():
        backup.replace(projection)
    elif backup.exists() or backup.is_symlink():
        _remove_path(backup)
    distorted = _distorted_paths(projection, binding.projection_hashes)
    archive = transport.export_tracked_files(remote_root)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{projection.name}.projection-", dir=projection.parent)
    )
    try:
        hashes = _extract_archive(archive, staging)
        control = projection / ".miniclaw2"
        if control.exists() or control.is_symlink():
            if control.is_symlink() or not control.is_dir():
                raise RemoteTransportError("本地投影的 .miniclaw2 控制目录无效")
        if projection.exists() or projection.is_symlink():
            projection.replace(backup)
        try:
            staging.replace(projection)
            previous_control = backup / ".miniclaw2"
            if previous_control.is_dir():
                previous_control.replace(projection / ".miniclaw2")
        except Exception:
            _remove_path(projection)
            if backup.exists():
                backup.replace(projection)
            raise
        _remove_path(backup)
    except Exception:
        _remove_path(staging)
        raise
    next_binding = binding.model_copy(
        update={
            "projection_hashes": hashes,
            "projection_synced_at": time.time(),
        }
    )
    return ProjectionSyncResult(
        binding=next_binding,
        distorted_paths=tuple(distorted),
        file_count=len(hashes),
    )


def _extract_archive(archive: bytes, destination: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    try:
        source = tarfile.open(fileobj=io.BytesIO(archive), mode="r:*")
    except (tarfile.TarError, OSError) as exc:
        raise RemoteTransportError(f"远端源码快照不是有效的 tar 归档：{exc}") from exc
    with source:
        for member in source:
            relative = _validated_member_path(member.name)
            if relative is None:
                continue
            target = destination.joinpath(*relative.parts)
            _reject_symlink_parent(destination, target)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if member.issym():
                _validate_symlink_target(relative, member.linkname)
                target.symlink_to(member.linkname)
                hashes[relative.as_posix()] = _symlink_digest(member.linkname)
                continue
            if not member.isfile():
                raise RemoteTransportError(
                    f"远端源码快照包含不支持的文件类型：{relative.as_posix()}"
                )
            stream = source.extractfile(member)
            if stream is None:
                raise RemoteTransportError(
                    f"无法读取远端源码快照文件：{relative.as_posix()}"
                )
            digest = hashlib.sha256()
            with target.open("wb") as output:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
                    output.write(block)
            target.chmod(0o755 if member.mode & 0o111 else 0o644)
            hashes[relative.as_posix()] = digest.hexdigest()
    return hashes


def _validated_member_path(name: str) -> PurePosixPath | None:
    normalized = name.removeprefix("./")
    if normalized in {"", "."}:
        return None
    relative = PurePosixPath(normalized)
    if relative.is_absolute() or ".." in relative.parts:
        raise RemoteTransportError(f"远端源码快照路径越界：{name}")
    if relative.parts[0] in {".git", ".miniclaw2"}:
        raise RemoteTransportError(f"远端源码快照包含保留路径：{name}")
    return relative


def _reject_symlink_parent(root: Path, target: Path) -> None:
    current = root
    for part in target.relative_to(root).parts[:-1]:
        current /= part
        if current.is_symlink():
            raise RemoteTransportError(f"远端源码快照路径经过符号链接：{target}")


def _validate_symlink_target(relative: PurePosixPath, linkname: str) -> None:
    link = PurePosixPath(linkname)
    if link.is_absolute():
        raise RemoteTransportError(
            f"远端源码快照包含绝对符号链接：{relative.as_posix()}"
        )
    resolved = PurePosixPath(
        posixpath.normpath((relative.parent / link).as_posix())
    )
    if not resolved.parts or resolved.parts[0] in {"..", ".miniclaw2"}:
        raise RemoteTransportError(
            f"远端源码快照符号链接越界：{relative.as_posix()}"
        )


def _distorted_paths(root: Path, expected: dict[str, str]) -> list[str]:
    distorted: list[str] = []
    for relative, digest in sorted(expected.items()):
        validated = _validated_member_path(relative)
        if validated is None:
            raise RemoteTransportError("本地投影状态包含无效路径")
        path = root.joinpath(*validated.parts)
        if _path_digest(path) != digest:
            distorted.append(relative)
    return distorted


def _path_digest(path: Path) -> str | None:
    if path.is_symlink():
        return _symlink_digest(os.readlink(path))
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _symlink_digest(target: str) -> str:
    return hashlib.sha256(f"symlink:{target}".encode()).hexdigest()


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
