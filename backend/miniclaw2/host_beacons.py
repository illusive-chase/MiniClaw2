"""Cross-host sync awareness derived from per-host beacon refs.

Each host publishes ``refs/heads/_hosts/<machine-id>`` from its own
user-triggered syncs (see ``SyncManager._push_with_beacon``). Every commit on
that branch carries one ``beacon.json``: the metadata commit the host had
integrated, when, and which MiniClaw2 it ran. A peer that has fetched the
branch compares it against its own ``origin/<branch>`` to answer "has that host
pulled my push yet?" and "how far behind is it?".

Everything here is a self-reported snapshot. It describes the peer as of its
last sync and never what it is doing now; surfaces must say "as of T (reported
by the host)". Nothing here gates what this host can do (PHILOSOPHY §12.5).
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .host_profile import HostProfile, load_profiles
from .sync import (
    BEACON_FILENAME,
    BEACON_TRACKING_PREFIX,
    SyncError,
    SyncManager,
    _git,
    host_email,
    machine_id_from_email,
)
from .sync_local import SyncLocal


#: Beacon history returned per host by the host list.
HISTORY_LIMIT = 30
#: Hosts silent for longer than this are faded in the timeline.
STALE_AFTER_SECONDS = 30 * 24 * 3600
#: A self-reported time this far after our first observation is suspicious.
CLOCK_SKEW_SECONDS = 300.0
_AUTHOR_LABEL = re.compile(r"^MiniClaw2 \((.+)\)$")
_SHA = re.compile(r"^[0-9a-f]{40}$")


@dataclass
class Beacon:
    sha: str
    committed_at: float | None
    machine_id: str
    integrated: str | None = None
    synced_at: float | None = None
    contributed: int = 0
    label: str | None = None
    store_schema: int | None = None
    miniclaw: dict[str, Any] = field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        return {
            "sha": self.sha,
            "integrated": self.integrated,
            "synced_at": self.synced_at,
            "contributed": self.contributed,
            "store_schema": self.store_schema,
            "miniclaw": self.miniclaw,
        }


def beacon_refs(root: Path) -> dict[str, str]:
    """Machine id → tracking-ref sha for every fetched beacon."""
    listed = _git(
        root, "for-each-ref", "--format=%(objectname) %(refname)", BEACON_TRACKING_PREFIX,
        check=False,
    )
    refs: dict[str, str] = {}
    for line in listed.stdout.splitlines():
        sha, _, ref = line.partition(" ")
        machine_id = ref.removeprefix(BEACON_TRACKING_PREFIX)
        if ref.startswith(BEACON_TRACKING_PREFIX) and machine_id and "/" not in machine_id:
            refs[machine_id] = sha
    return refs


def beacon_history(root: Path, machine_id: str, *, limit: int = HISTORY_LIMIT) -> list[Beacon]:
    """Newest-first beacons on one host's branch, read in two git processes."""
    ref = BEACON_TRACKING_PREFIX + machine_id
    listed = _git(root, "log", f"-n{limit}", "--format=%H %ct", ref, "--", check=False)
    commits: list[tuple[str, float | None]] = []
    for line in listed.stdout.splitlines():
        sha, _, stamp = line.partition(" ")
        if _SHA.match(sha):
            commits.append((sha, float(stamp) if stamp.strip().isdigit() else None))
    if not commits:
        return []
    payloads = _cat_beacons(root, [sha for sha, _ in commits])
    history: list[Beacon] = []
    for sha, committed_at in commits:
        payload = payloads.get(sha)
        if payload is None or payload.get("machine_id") != machine_id:
            continue
        history.append(_beacon(sha, committed_at, machine_id, payload))
    return history


def _cat_beacons(root: Path, shas: list[str]) -> dict[str, dict[str, Any]]:
    request = "".join(f"{sha}:{BEACON_FILENAME}\n" for sha in shas).encode()
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "cat-file", "--batch"], input=request,
            capture_output=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    output = result.stdout
    payloads: dict[str, dict[str, Any]] = {}
    position = 0
    for sha in shas:
        newline = output.find(b"\n", position)
        if newline < 0:
            break
        header = output[position:newline].decode(errors="replace").split()
        position = newline + 1
        if len(header) != 3 or header[1] != "blob":
            continue
        size = int(header[2])
        body = output[position:position + size]
        position += size + 1
        try:
            value = json.loads(body.decode("utf-8"))
        except ValueError:
            continue
        if isinstance(value, dict):
            payloads[sha] = value
    return payloads


def _beacon(sha: str, committed_at: float | None, machine_id: str, payload: dict[str, Any]) -> Beacon:
    def number(key: str) -> float | None:
        value = payload.get(key)
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    integrated = payload.get("integrated")
    miniclaw = payload.get("miniclaw")
    store_schema = payload.get("store_schema")
    contributed = payload.get("contributed")
    label = payload.get("label")
    return Beacon(
        sha=sha,
        committed_at=committed_at,
        machine_id=machine_id,
        integrated=integrated if isinstance(integrated, str) and _SHA.match(integrated) else None,
        synced_at=number("synced_at"),
        contributed=contributed if type(contributed) is int else 0,
        label=label.strip() if isinstance(label, str) and label.strip() else None,
        store_schema=store_schema if type(store_schema) is int else None,
        miniclaw=miniclaw if isinstance(miniclaw, dict) else {},
    )


def _is_ancestor(root: Path, ancestor: str, descendant: str) -> bool | None:
    result = _git(root, "merge-base", "--is-ancestor", ancestor, descendant, check=False)
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    return None


def _count(root: Path, *args: str) -> int | None:
    result = _git(root, "rev-list", "--count", *args, check=False)
    return int(result.stdout.strip()) if result.returncode == 0 and result.stdout.strip().isdigit() else None


def _missing_by(root: Path, integrated: str, tip: str) -> dict[str, int] | None:
    listed = _git(root, "log", "--no-merges", "--format=%ae", f"{integrated}..{tip}", check=False)
    if listed.returncode != 0:
        return None
    counts: Counter[str] = Counter()
    for email in listed.stdout.split():
        counts[machine_id_from_email(email) or ""] += 1
    return dict(counts)


def _project_host_labels(root: Path) -> dict[str, tuple[float, str]]:
    """Newest creation-time label each host left in a project partition."""
    labels: dict[str, tuple[float, str]] = {}
    for path in root.glob("projects/*/hosts/*/host.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        label = payload.get("label")
        bound_at = payload.get("bound_at")
        if not isinstance(label, str) or not label.strip():
            continue
        stamp = float(bound_at) if isinstance(bound_at, (int, float)) else 0.0
        machine_id = path.parent.name
        if machine_id not in labels or stamp >= labels[machine_id][0]:
            labels[machine_id] = (stamp, label.strip())
    for path in root.glob("projects/*/hosts/*"):
        if path.is_dir() and path.name not in labels:
            labels[path.name] = (0.0, "")
    return labels


def _last_push(root: Path, tip: str, machine_id: str) -> tuple[float | None, str | None]:
    """When a host's newest commit on the shared branch was made, and its name."""
    listed = _git(
        root, "log", "-1", "--format=%ct%x00%an", "--fixed-strings",
        f"--author=<{host_email(machine_id)}>", tip, "--", check=False,
    )
    stamp, _, name = listed.stdout.strip().partition("\x00")
    match = _AUTHOR_LABEL.match(name.strip())
    return (float(stamp) if stamp.isdigit() else None), (match.group(1) if match else None)


def host_label(
    machine_id: str,
    profiles: dict[str, HostProfile],
    *,
    beacon_label: str | None = None,
    partition_label: str | None = None,
    author_label: str | None = None,
) -> str:
    profile = profiles.get(machine_id)
    if profile is not None:
        return profile.label
    return beacon_label or partition_label or author_label or machine_id[:8]


def _record_first_seen(local: SyncLocal, beacons: dict[str, Beacon], now: float) -> dict[str, float]:
    seen = local.section("beacons")
    first_seen: dict[str, float] = {}
    changes: dict[str, Any] = {}
    for machine_id, beacon in beacons.items():
        entry = seen.get(machine_id)
        if isinstance(entry, dict) and entry.get("sha") == beacon.sha and isinstance(entry.get("first_seen_at"), (int, float)):
            first_seen[machine_id] = float(entry["first_seen_at"])
            continue
        first_seen[machine_id] = now
        changes[machine_id] = {"sha": beacon.sha, "first_seen_at": now}
    if changes:
        local.update_section("beacons", changes)
    return first_seen


def _source_relation(source_repo: Path | None, head: object) -> dict[str, Any]:
    """Compare a peer's reported source head with what this host knows."""
    relation: dict[str, Any] = {"head": head if isinstance(head, str) else None, "behind_upstream": None}
    if source_repo is None or not isinstance(head, str) or not _SHA.match(head):
        return relation
    try:
        upstream = _git(source_repo, "rev-parse", "--verify", "--quiet", "@{upstream}", check=False)
    except SyncError:
        return relation
    tip = upstream.stdout.strip()
    if upstream.returncode != 0 or not tip:
        return relation
    relation["behind_upstream"] = 0 if tip == head else _count(source_repo, f"{head}..{tip}")
    return relation


def list_hosts(
    root: Path,
    sync: SyncManager,
    local: SyncLocal,
    *,
    source_repo: Path | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Every host this store knows of, with what can be derived about it."""
    now = time.time() if now is None else now
    profiles = load_profiles(root)
    refs = beacon_refs(root)
    histories = {machine_id: beacon_history(root, machine_id) for machine_id in refs}
    latest = {machine_id: history[0] for machine_id, history in histories.items() if history}
    first_seen = _record_first_seen(local, latest, now)
    partitions = _project_host_labels(root)
    self_id = sync.identity.id
    branch = sync._branch()
    tip_result = _git(root, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}", check=False)
    tip = tip_result.stdout.strip() if tip_result.returncode == 0 else None
    my_push = sync.identity.last_synced_commit
    ids = {self_id, *profiles, *refs, *partitions}
    hosts: list[dict[str, Any]] = []
    labels_by_id: dict[str, str] = {}
    for machine_id in sorted(ids):
        beacon = latest.get(machine_id)
        partition_label = partitions.get(machine_id, (0.0, ""))[1] or None
        last_push_at, author_label = (None, None)
        if tip is not None and (beacon is None or machine_id not in profiles):
            last_push_at, author_label = _last_push(root, tip, machine_id)
        label = host_label(
            machine_id, profiles,
            beacon_label=beacon.label if beacon else None,
            partition_label=partition_label, author_label=author_label,
        )
        if machine_id == self_id:
            label = profiles[machine_id].label if machine_id in profiles else sync.identity.label
        labels_by_id[machine_id] = label
        profile = profiles.get(machine_id)
        entry: dict[str, Any] = {
            "machine_id": machine_id,
            "is_self": machine_id == self_id,
            "label": label,
            "icon": profile.icon if profile else None,
            "color": profile.color if profile else None,
            "hostname": profile.hostname if profile else None,
            "has_profile": profile is not None,
            "beacon": None,
            "history": [item.payload() for item in histories.get(machine_id, [])],
            "status": "self" if machine_id == self_id else "no_beacon",
            "behind": None,
            "missing_by": [],
            "includes_my_push": None,
            "source": None,
            "last_push_at": last_push_at,
            "stale": False,
        }
        if beacon is not None:
            observed = first_seen.get(machine_id, now)
            entry["beacon"] = {
                **beacon.payload(),
                "first_seen_at": observed,
                "clock_skew": beacon.synced_at is not None and beacon.synced_at - observed > CLOCK_SKEW_SECONDS,
            }
            entry["source"] = _source_relation(source_repo, beacon.miniclaw.get("head"))
            if machine_id != self_id and tip is not None and beacon.integrated:
                behind = 0 if beacon.integrated == tip else _count(root, f"{beacon.integrated}..{tip}")
                entry["behind"] = behind
                entry["status"] = "unknown" if behind is None else "current" if behind == 0 else "behind"
                if behind:
                    missing = _missing_by(root, beacon.integrated, tip) or {}
                    entry["missing_by"] = [
                        {"machine_id": owner, "count": count}
                        for owner, count in sorted(missing.items(), key=lambda item: -item[1])
                    ]
                if my_push:
                    entry["includes_my_push"] = (
                        True if my_push == beacon.integrated else _is_ancestor(root, my_push, beacon.integrated)
                    )
            elif machine_id != self_id:
                entry["status"] = "unknown"
        last_seen = (beacon.synced_at if beacon and beacon.synced_at else None) or last_push_at
        entry["stale"] = machine_id != self_id and (last_seen is None or now - last_seen > STALE_AFTER_SECONDS)
        hosts.append(entry)
    for entry in hosts:
        for missing in entry["missing_by"]:
            owner = missing["machine_id"]
            missing["label"] = labels_by_id.get(owner) or (owner[:8] if owner else "未知")
            missing["is_self"] = owner == self_id
    hosts.sort(key=lambda item: (not item["is_self"], item["stale"], item["label"].lower()))
    return {
        "generated_at": now,
        "remote_tip": tip,
        "self_id": self_id,
        "last_synced_commit": my_push,
        "hosts": hosts,
    }


def host_timeline(root: Path, machine_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
    return [item.payload() for item in beacon_history(root, machine_id, limit=limit)]
