"""Native Agent Skills library, import, audit, and materialization."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .contextspace import contextspace_root


_SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_IMPORT_METADATA = "skill-imports.json"
_VCS_METADATA_NAMES = {".git", ".hg", ".svn", ".bzr"}


class SkillError(ValueError):
    """A user-facing skill validation or import failure."""


@dataclass(slots=True)
class SkillMaterialization:
    provider: str
    root: Path | None = None
    plugin_dir: Path | None = None
    extra_roots: list[str] = field(default_factory=list)
    env_overrides: dict[str, str] = field(default_factory=dict)
    audit: list[dict[str, Any]] = field(default_factory=list)

    @property
    def suggestions(self) -> list[str]:
        return [
            f'The skill "{item["name"]}" is available and likely relevant to this task.'
            for item in self.audit
            if item.get("suggest") and not item.get("missing") and not item.get("failed")
        ]

    @property
    def failed_suggestions(self) -> list[str]:
        return [
            f'The skill "{item["name"]}" is available and likely relevant to this task.'
            for item in self.audit
            if item.get("suggest") and item.get("failed")
        ]


def normalize_skill_selections(raw: Any) -> list[dict[str, Any]]:
    """Normalize string or structured skill selections, preserving order."""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    indexes: dict[str, int] = {}
    for entry in raw:
        suggest = False
        auto_attached = False
        required_by: str | None = None
        attachment_reason: str | None = None
        if isinstance(entry, str):
            raw_id = entry
        elif isinstance(entry, dict):
            raw_id = entry.get("id")
            suggest = entry.get("suggest") is True
            auto_attached = entry.get("auto_attached") is True
            if isinstance(entry.get("required_by"), str):
                required_by = entry["required_by"]
            if entry.get("attachment_reason") in {"dependency", "package"}:
                attachment_reason = entry["attachment_reason"]
        else:
            continue
        if not isinstance(raw_id, str):
            continue
        cleaned = raw_id.strip()
        if cleaned.startswith("skills."):
            slug = cleaned[len("skills."):]
        elif "." not in cleaned:
            slug = cleaned
        else:
            continue
        if not _SLUG_RE.fullmatch(slug):
            continue
        skill_id = f"skills.{slug}"
        existing = indexes.get(skill_id)
        if existing is not None:
            out[existing]["suggest"] = out[existing]["suggest"] or suggest
            if not auto_attached:
                out[existing].pop("auto_attached", None)
                out[existing].pop("required_by", None)
                out[existing].pop("attachment_reason", None)
            continue
        indexes[skill_id] = len(out)
        item: dict[str, Any] = {"id": skill_id, "suggest": suggest}
        if auto_attached:
            item["auto_attached"] = True
            if required_by:
                item["required_by"] = required_by
            if attachment_reason:
                item["attachment_reason"] = attachment_reason
        out.append(item)
    return out


def expand_skill_selections(
    raw: Any,
    *,
    store_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Expand explicit selections with package members and sibling dependencies.

    Auto-attached entries persisted by an earlier expansion are not roots. This
    makes removing the last explicit package member remove the generated entries
    on the next normalization pass instead of turning them into permanent picks.
    """
    normalized = normalize_skill_selections(raw)
    explicit = [item for item in normalized if not item.get("auto_attached")]
    if not explicit:
        return []

    root = contextspace_root(store_root)
    library = root / "skills"
    provenance = _read_import_metadata(root)
    out = [dict(item) for item in explicit]
    indexes = {item["id"]: index for index, item in enumerate(out)}
    cursor = 0
    while cursor < len(out):
        selection = out[cursor]
        cursor += 1
        slug = selection["id"][len("skills."):]
        record = provenance.get(slug, {})
        additions: list[tuple[str, str]] = []

        if record.get("auto_attach_package") is True:
            members = record.get("package_members")
            if isinstance(members, list):
                additions.extend((member, "package") for member in members)

        skill_dir = library / slug
        if skill_dir.is_dir():
            try:
                inspected = inspect_agent_skill(skill_dir, context_root=root)
            except SkillError:
                inspected = {}
            dependencies = inspected.get("dependencies")
            if isinstance(dependencies, list):
                additions.extend((dependency, "dependency") for dependency in dependencies)

        for raw_id, reason in additions:
            dependency_id = _normalize_selection_id(raw_id)
            if dependency_id is None or dependency_id in indexes:
                continue
            indexes[dependency_id] = len(out)
            out.append({
                "id": dependency_id,
                "suggest": False,
                "auto_attached": True,
                "required_by": selection["id"],
                "attachment_reason": reason,
            })
    return out


def list_agent_skills(store_root: Path | None = None) -> list[dict[str, Any]]:
    root = contextspace_root(store_root)
    library = root / "skills"
    provenance = _read_import_metadata(root)
    if not library.exists():
        return []
    result: list[dict[str, Any]] = []
    for skill_dir in sorted(library.iterdir()):
        if not skill_dir.is_dir() or not _SLUG_RE.fullmatch(skill_dir.name):
            continue
        try:
            summary = inspect_agent_skill(skill_dir, context_root=root)
        except SkillError:
            continue
        summary.pop("body", None)
        summary.update(provenance.get(skill_dir.name, {}))
        result.append(summary)
    return result


def get_agent_skill(
    slug: str, *, store_root: Path | None = None
) -> dict[str, Any] | None:
    normalized = _selection_slug(slug)
    root = contextspace_root(store_root)
    skill_dir = root / "skills" / normalized
    if not skill_dir.is_dir():
        return None
    detail = inspect_agent_skill(skill_dir, context_root=root)
    detail.update(_read_import_metadata(root).get(normalized, {}))
    return detail


def inspect_agent_skill(
    skill_dir: Path, *, context_root: Path | None = None
) -> dict[str, Any]:
    skill_md = skill_dir / "SKILL.md"
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillError(f"cannot read {skill_md}: {exc}") from exc
    metadata, body = _parse_skill_markdown(text, skill_md)
    slug = skill_dir.name
    files = [
        path.relative_to(skill_dir).as_posix()
        for path in sorted(skill_dir.rglob("*"))
        if path.is_file() and not path.is_symlink()
    ]
    display_path = str(skill_dir)
    if context_root is not None:
        try:
            display_path = skill_dir.relative_to(context_root).as_posix()
        except ValueError:
            pass
    return {
        "id": f"skills.{slug}",
        "kind": "skill",
        "slug": slug,
        "name": metadata["name"],
        "title": metadata["name"],
        "description": metadata["description"],
        "path": display_path,
        "files": files,
        "body": body,
        "content_hash": skill_content_hash(skill_dir),
        "version": metadata.get("version"),
        "dependencies": metadata.get("dependencies", []),
    }


def skill_content_hash(skill_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(skill_dir.rglob("*")):
        relative = path.relative_to(skill_dir).as_posix()
        if path.is_symlink():
            raise SkillError(f"skill contains a symbolic link: {relative}")
        if not path.is_file():
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        try:
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError as exc:
            raise SkillError(f"cannot hash skill file {path}: {exc}") from exc
        digest.update(b"\0")
    return digest.hexdigest()


def import_agent_skill(
    source: str,
    *,
    store_root: Path | None = None,
    slug: str | None = None,
) -> dict[str, Any]:
    """Import one skill, or auto-detect and atomically import a skill package."""
    source = source.strip()
    if not source:
        raise SkillError("skill import source is required")
    root = contextspace_root(store_root)
    library = root / "skills"
    library.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="miniclaw2-skill-import-") as temp:
        temp_root = Path(temp)
        unpacked, source_kind = _materialize_import_source(source, temp_root)
        candidates = _find_skill_directories(unpacked)
        if slug is not None and len(candidates) > 1:
            requested = _selection_slug(slug)
            matches = [candidate for candidate in candidates if candidate.name == requested]
            if len(matches) != 1:
                raise SkillError(f"skill import source has no unique skill named {requested}")
            candidates = matches
        if len(candidates) == 1:
            candidate = candidates[0]
            summary = inspect_agent_skill(candidate)
            inferred_slug = candidate.name
            if source_kind in {"zip", "git"} and candidate == unpacked:
                inferred_slug = ""
            target_slug = _normalize_import_slug(slug or inferred_slug, summary["name"])
            targets = _install_skill_candidates(
                library,
                [(target_slug, candidate)],
            )
            package_id = None
        else:
            named_candidates: list[tuple[str, Path]] = []
            seen: set[str] = set()
            for candidate in candidates:
                target_slug = _selection_slug(candidate.name)
                if target_slug in seen:
                    raise SkillError(
                        f"skill package contains duplicate slug: {target_slug}"
                    )
                seen.add(target_slug)
                named_candidates.append((target_slug, candidate))
            targets = _install_skill_candidates(library, named_candidates)
            package_id = _skill_package_id(source)

    imported_at = time.time()
    metadata = _read_import_metadata(root)
    members = sorted(targets)
    target_ids = {f"skills.{target_slug}" for target_slug in targets}
    for existing_slug, record in metadata.items():
        if existing_slug in targets:
            continue
        existing_members = record.get("package_members")
        if not isinstance(existing_members, list):
            continue
        retained_members = [
            member for member in existing_members if member not in target_ids
        ]
        if retained_members != existing_members:
            record["package_members"] = retained_members
    if package_id is not None:
        for existing_slug, record in metadata.items():
            if record.get("package_id") != package_id or existing_slug in targets:
                continue
            for key in (
                "package_id",
                "package_members",
                "auto_attach_package",
            ):
                record.pop(key, None)
    for target_slug in targets:
        record: dict[str, Any] = {
            "import_source": source,
            "import_kind": source_kind,
            "imported_at": imported_at,
        }
        if package_id is not None:
            record.update({
                "package_id": package_id,
                "package_members": [f"skills.{member}" for member in members],
                "auto_attach_package": True,
            })
        metadata[target_slug] = record
    _write_import_metadata(root, metadata)

    imported: list[dict[str, Any]] = []
    for target_slug, target in targets.items():
        summary = inspect_agent_skill(target, context_root=root)
        summary.update(metadata[target_slug])
        imported.append(summary)
    imported.sort(key=lambda item: item["slug"])
    if package_id is None:
        return imported[0]
    return {
        "kind": "skill-pack",
        "package_id": package_id,
        "source": source,
        "count": len(imported),
        "skills": imported,
    }


def record_authored_agent_skill(
    slug: str,
    *,
    node_id: str,
    store_root: Path | None = None,
) -> None:
    """Record provenance for a newly librarian-authored skill."""
    normalized = _selection_slug(slug)
    root = contextspace_root(store_root)
    metadata = _read_import_metadata(root)
    metadata[normalized] = {
        "import_source": f"node:{node_id}",
        "import_kind": "authored",
        "imported_at": time.time(),
    }
    _write_import_metadata(root, metadata)


def delete_agent_skill(slug: str, *, store_root: Path | None = None) -> bool:
    normalized = _selection_slug(slug)
    root = contextspace_root(store_root)
    library = (root / "skills").resolve()
    target = (library / normalized).resolve()
    if target.parent != library or not target.is_dir():
        return False
    shutil.rmtree(target)
    metadata = _read_import_metadata(root)
    changed = metadata.pop(normalized, None) is not None
    deleted_id = f"skills.{normalized}"
    for record in metadata.values():
        members = record.get("package_members")
        if not isinstance(members, list) or deleted_id not in members:
            continue
        record["package_members"] = [member for member in members if member != deleted_id]
        changed = True
    if changed:
        _write_import_metadata(root, metadata)
    return True


def materialize_agent_skills(
    raw: Any,
    *,
    provider: str,
    store_root: Path,
    workspace_root: Path,
) -> SkillMaterialization:
    selections = expand_skill_selections(raw, store_root=store_root)
    result = SkillMaterialization(provider=provider)
    if not selections:
        return result
    root = contextspace_root(store_root)
    library = root / "skills"
    if provider == "claude":
        mechanism = "claude-plugin-dir"
    elif provider == "codex":
        mechanism = "codex-extra-roots"
    else:
        mechanism = f"unsupported-{provider}"
    resolved: list[tuple[dict[str, Any], Path, dict[str, Any]]] = []
    for selection in selections:
        slug = selection["id"][len("skills."):]
        source = library / slug
        audit: dict[str, Any] = {
            "id": selection["id"],
            "slug": slug,
            "name": slug,
            "suggest": selection["suggest"],
            "resolved_path": str(source),
            "mechanism": mechanism,
            "missing": False,
            "failed": False,
            "used": False,
        }
        for key in ("auto_attached", "required_by", "attachment_reason"):
            if key in selection:
                audit[key] = selection[key]
        if not source.is_dir():
            audit["missing"] = True
            result.audit.append(audit)
            continue
        try:
            inspected = inspect_agent_skill(source, context_root=root)
            audit["name"] = inspected["name"]
            audit["content_hash"] = inspected["content_hash"]
            audit["dependencies"] = inspected["dependencies"]
        except Exception as exc:  # noqa: BLE001
            audit["failed"] = True
            audit["error"] = str(exc)
            result.audit.append(audit)
            continue
        result.audit.append(audit)
        resolved.append((selection, source, audit))

    if not resolved:
        return result
    if provider == "codex":
        for _selection, source, audit in resolved:
            resolved_source = str(source.resolve())
            result.extra_roots.append(resolved_source)
            audit["materialized_path"] = resolved_source
        return result

    try:
        if provider != "claude":
            raise SkillError(f"unsupported skill provider: {provider}")
        target_root = workspace_root / "skill-plugin"
        skills_target = target_root / "skills"
        result.root = target_root
        result.plugin_dir = target_root
        skills_target.mkdir(parents=True, exist_ok=True)
        for _selection, source, audit in resolved:
            try:
                shutil.copytree(source, skills_target / audit["slug"])
                audit["materialized_path"] = str(skills_target / audit["slug"])
            except Exception as exc:  # noqa: BLE001
                audit["failed"] = True
                audit["error"] = str(exc)
    except Exception as exc:  # noqa: BLE001
        for _selection, _source, audit in resolved:
            audit["failed"] = True
            audit["error"] = str(exc)
        result.root = None
        result.plugin_dir = None
        result.extra_roots.clear()
        result.env_overrides.clear()
    return result


def _parse_skill_markdown(text: str, path: Path) -> tuple[dict[str, Any], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillError(f"{path} must start with YAML frontmatter")
    try:
        closing = next(index for index in range(1, len(lines)) if lines[index].strip() == "---")
    except StopIteration as exc:
        raise SkillError(f"{path} has unterminated YAML frontmatter") from exc
    try:
        raw = yaml.safe_load("\n".join(lines[1:closing]))
    except yaml.YAMLError as exc:
        raise SkillError(f"invalid frontmatter in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise SkillError(f"frontmatter in {path} must be a mapping")
    metadata: dict[str, Any] = {}
    for key in ("name", "description"):
        value = raw.get(key)
        if not isinstance(value, str) or not value.strip():
            raise SkillError(f"frontmatter in {path} requires non-empty {key}")
        metadata[key] = value.strip()
    version = raw.get("version")
    if isinstance(version, (str, int, float)):
        metadata["version"] = str(version)
    metadata["dependencies"] = _frontmatter_sibling_dependencies(raw, path)
    return metadata, "\n".join(lines[closing + 1:]).lstrip("\n")


def _frontmatter_sibling_dependencies(
    frontmatter: dict[str, Any],
    path: Path,
) -> list[str]:
    metadata = frontmatter.get("metadata")
    if metadata is None:
        return []
    if not isinstance(metadata, dict):
        raise SkillError(f"metadata in {path} must be a mapping")
    requires = metadata.get("requires")
    if requires is None:
        return []
    if not isinstance(requires, dict):
        raise SkillError(f"metadata.requires in {path} must be a mapping")
    siblings = requires.get("siblings")
    if siblings is None:
        return []
    if not isinstance(siblings, list):
        raise SkillError(f"metadata.requires.siblings in {path} must be a list")
    result: list[str] = []
    seen: set[str] = set()
    for sibling in siblings:
        dependency_id = _normalize_selection_id(sibling)
        if dependency_id is None:
            raise SkillError(f"invalid sibling dependency in {path}: {sibling!r}")
        if dependency_id not in seen:
            seen.add(dependency_id)
            result.append(dependency_id)
    return result


def _materialize_import_source(source: str, temp_root: Path) -> tuple[Path, str]:
    local = Path(source).expanduser()
    if local.exists():
        if local.is_dir():
            return local.resolve(), "local"
        if zipfile.is_zipfile(local):
            target = temp_root / "archive"
            _extract_zip(local, target)
            return target, "zip"
        raise SkillError(f"unsupported skill import file: {local}")
    if "://" in source or source.startswith("git@") or source.endswith(".git"):
        target = temp_root / "repository"
        try:
            completed = subprocess.run(
                ["git", "clone", "--depth", "1", source, str(target)],
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SkillError(f"git skill import failed: {exc}") from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise SkillError(f"git skill import failed: {detail}")
        return target, "git"
    raise SkillError(f"skill import source does not exist: {source}")


def _extract_zip(source: Path, target: Path) -> None:
    target.mkdir(parents=True)
    root = target.resolve()
    with zipfile.ZipFile(source) as archive:
        for member in archive.infolist():
            destination = (target / member.filename).resolve()
            if destination != root and root not in destination.parents:
                raise SkillError(f"zip member escapes import root: {member.filename}")
        archive.extractall(target)


def _find_skill_directories(root: Path) -> list[Path]:
    candidates = sorted({path.parent for path in root.rglob("SKILL.md")})
    if not candidates:
        raise SkillError("import source contains no SKILL.md")
    return candidates


def _install_skill_candidates(
    library: Path,
    candidates: list[tuple[str, Path]],
) -> dict[str, Path]:
    """Validate and atomically replace all candidate directories as one unit."""
    for _slug, candidate in candidates:
        _validate_tree(candidate)
        inspect_agent_skill(candidate)

    staged: dict[str, Path] = {}
    backups: dict[str, Path] = {}
    installed: list[str] = []
    try:
        for slug, candidate in candidates:
            staging = library / f".{slug}.importing"
            backup = library / f".{slug}.previous"
            if staging.exists():
                shutil.rmtree(staging)
            if backup.exists():
                shutil.rmtree(backup)
            shutil.copytree(candidate, staging, ignore=_ignore_vcs_metadata)
            staged[slug] = staging
            backups[slug] = backup

        for slug, _candidate in candidates:
            target = library / slug
            backup = backups[slug]
            if target.exists():
                target.replace(backup)
            staged[slug].replace(target)
            installed.append(slug)
    except Exception:
        for slug in reversed(installed):
            target = library / slug
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            backup = backups[slug]
            if backup.exists():
                backup.replace(target)
        for slug, staging in staged.items():
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            backup = backups[slug]
            target = library / slug
            if backup.exists() and not target.exists():
                backup.replace(target)
        raise
    else:
        for backup in backups.values():
            if backup.exists():
                shutil.rmtree(backup)
    return {slug: library / slug for slug, _candidate in candidates}


def _skill_package_id(source: str) -> str:
    digest = hashlib.sha256(source.strip().encode("utf-8")).hexdigest()[:12]
    return f"skill-pack-{digest}"


def _validate_tree(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_symlink():
            raise SkillError(f"skill imports may not contain symlinks: {path}")


def _ignore_vcs_metadata(_directory: str, names: list[str]) -> set[str]:
    return _VCS_METADATA_NAMES.intersection(names)


def _normalize_import_slug(value: str, fallback_name: str) -> str:
    cleaned = value.strip()
    if cleaned.startswith("skills."):
        cleaned = cleaned[len("skills."):]
    if _SLUG_RE.fullmatch(cleaned):
        return cleaned
    generated = re.sub(r"[^a-z0-9]+", "-", fallback_name.lower()).strip("-")
    if not _SLUG_RE.fullmatch(generated):
        raise SkillError(f"cannot derive a valid skill slug from {value!r}")
    return generated


def _selection_slug(value: str) -> str:
    if not isinstance(value, str):
        raise SkillError(f"invalid skill slug: {value!r}")
    cleaned = value.strip()
    if cleaned.startswith("skills."):
        cleaned = cleaned[len("skills."):]
    if not _SLUG_RE.fullmatch(cleaned):
        raise SkillError(f"invalid skill slug: {value!r}")
    return cleaned


def _normalize_selection_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if cleaned.startswith("skills."):
        slug = cleaned[len("skills."):]
    elif "." not in cleaned:
        slug = cleaned
    else:
        return None
    if not _SLUG_RE.fullmatch(slug):
        return None
    return f"skills.{slug}"


def _metadata_path(root: Path) -> Path:
    return root / _IMPORT_METADATA


def _read_import_metadata(root: Path) -> dict[str, dict[str, Any]]:
    path = _metadata_path(root)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        key: value
        for key, value in raw.items()
        if isinstance(key, str) and isinstance(value, dict)
    }


def _write_import_metadata(root: Path, payload: dict[str, dict[str, Any]]) -> None:
    path = _metadata_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)
