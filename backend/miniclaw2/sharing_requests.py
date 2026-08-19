"""Cross-device sharing requests for projects that are still device-native.

Records live outside the project tree at::

    $MINICLAW_HOME/sharing-requests/
      <project-id>/
        <request-id>/
          request.json       # requester-owned, immutable once written
          cancellation.json  # requester-owned, optional
          decision.json      # native-host-owned, optional

The project tree stays single-writer: a non-host device never writes under
``projects/<pid>/``. Requester and native host always touch different files,
so a request and a concurrent host-side project edit merge without conflict,
and deleting a project leaves the request as a stable orphaned record rather
than reviving a partial project directory.

A request is a project-level intent to migrate to ``shared``, not an ACL
entry for one device. Accepting any request converts the whole project, and
rejecting one does not bar that device from binding later.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from .domain import Project


logger = logging.getLogger(__name__)

_ModelT = TypeVar("_ModelT", bound=BaseModel)

SHARING_REQUESTS_DIRNAME = "sharing-requests"
REQUEST_FILENAME = "request.json"
CANCELLATION_FILENAME = "cancellation.json"
DECISION_FILENAME = "decision.json"

SharingDecisionValue = Literal["accepted", "rejected"]

#: Terminal statuses never become ``pending`` again for the same request.
SharingRequestStatus = Literal[
    "pending",
    "fulfilled",
    "rejected",
    "cancelled",
    "orphaned",
    "invalid",
]

OPEN_STATUSES: frozenset[str] = frozenset({"pending", "invalid"})


def _safe_segment(value: str, field: str) -> str:
    normalized = value.strip()
    if not normalized or Path(normalized).name != normalized:
        raise ValueError(f"invalid {field}: {value!r}")
    return normalized


class SharingRequest(BaseModel):
    """A non-host device asking the native host to enable sharing."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    id: str
    project_id: str
    observed_owner_machine_id: str
    requester_machine_id: str
    requester_machine_label: str = ""
    requested_at: float

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        return _safe_segment(value, "request id")

    @field_validator("project_id")
    @classmethod
    def _validate_project_id(cls, value: str) -> str:
        return _safe_segment(value, "project id")

    @field_validator("observed_owner_machine_id", "requester_machine_id")
    @classmethod
    def _validate_machine_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("machine id must not be empty")
        return normalized


class SharingCancellation(BaseModel):
    """The requester withdrawing its own request."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    request_id: str
    cancelled_by_machine_id: str
    cancelled_at: float


class SharingDecision(BaseModel):
    """The native host's answer.

    An ``accepted`` decision is written only after ``enable_sharing()``
    succeeds. The project actually being ``shared`` is the authoritative
    fact; this record alone never means the migration happened.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    request_id: str
    decision: SharingDecisionValue
    decided_by_machine_id: str
    decided_at: float


@dataclass(frozen=True)
class SharingRequestRecord:
    """A request plus the normalized status derived from project state."""

    request: SharingRequest
    status: str
    project_name: str = ""
    owner_machine_label: str = ""
    cancellation: SharingCancellation | None = None
    decision: SharingDecision | None = None

    @property
    def id(self) -> str:
        return self.request.id

    @property
    def project_id(self) -> str:
        return self.request.project_id

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES


def sharing_requests_root(store_root: Path) -> Path:
    return store_root / SHARING_REQUESTS_DIRNAME


def project_requests_dir(store_root: Path, pid: str) -> Path:
    return sharing_requests_root(store_root) / _safe_segment(pid, "project id")


def request_dir(store_root: Path, pid: str, rid: str) -> Path:
    return project_requests_dir(store_root, pid) / _safe_segment(rid, "request id")


def new_request_id() -> str:
    """Random ids let two offline devices request the same project safely."""
    return uuid4().hex


def write_record(path: Path, payload: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload.model_dump(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _read_model(path: Path, model: type[_ModelT]) -> _ModelT | None:
    if not path.is_file():
        return None
    try:
        return model.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, ValidationError):
        logger.error("ignoring invalid sharing request record %s", path, exc_info=True)
        return None


def load_request(store_root: Path, pid: str, rid: str) -> SharingRequest | None:
    """Read one request, rejecting payloads that disagree with their path."""
    try:
        path = request_dir(store_root, pid, rid) / REQUEST_FILENAME
    except ValueError:
        return None
    request = _read_model(path, SharingRequest)
    if request is None:
        return None
    if request.project_id != pid or request.id != rid:
        logger.error("sharing request %s disagrees with its own path", path)
        return None
    return request


def load_records(
    store_root: Path,
    projects: dict[str, Project],
    *,
    only_project_id: str | None = None,
) -> list[SharingRequestRecord]:
    """Read every request and normalize it against current project state."""
    root = sharing_requests_root(store_root)
    if not root.is_dir():
        return []
    if only_project_id is None:
        project_dirs = [entry for entry in sorted(root.iterdir()) if entry.is_dir()]
    else:
        try:
            candidate = project_requests_dir(store_root, only_project_id)
        except ValueError:
            return []
        project_dirs = [candidate] if candidate.is_dir() else []
    records: list[SharingRequestRecord] = []
    for project_dir in project_dirs:
        for entry in sorted(project_dir.iterdir()):
            if not entry.is_dir():
                continue
            request = load_request(store_root, project_dir.name, entry.name)
            if request is None:
                continue
            records.append(
                _record(store_root, request, projects.get(request.project_id))
            )
    records.sort(key=lambda record: (record.request.requested_at, record.request.id))
    return records


def _record(
    store_root: Path,
    request: SharingRequest,
    project: Project | None,
) -> SharingRequestRecord:
    directory = request_dir(store_root, request.project_id, request.id)
    cancellation = _read_model(directory / CANCELLATION_FILENAME, SharingCancellation)
    if cancellation is not None and (
        cancellation.request_id != request.id
        or cancellation.cancelled_by_machine_id != request.requester_machine_id
    ):
        logger.error(
            "ignoring sharing cancellation not written by the requester: %s",
            directory / CANCELLATION_FILENAME,
        )
        cancellation = None
    decision = _read_model(directory / DECISION_FILENAME, SharingDecision)
    if decision is not None and (
        decision.request_id != request.id
        or project is None
        or decision.decided_by_machine_id != project.machine_id
    ):
        logger.error(
            "ignoring sharing decision not written by the project owner: %s",
            directory / DECISION_FILENAME,
        )
        decision = None
    return SharingRequestRecord(
        request=request,
        status=normalize_status(
            request,
            project=project,
            cancellation=cancellation,
            decision=decision,
        ),
        project_name=project.name if project is not None else "",
        owner_machine_label=(
            (project.machine_label or project.machine_id)
            if project is not None
            else request.observed_owner_machine_id
        ),
        cancellation=cancellation,
        decision=decision,
    )


def normalize_status(
    request: SharingRequest,
    *,
    project: Project | None,
    cancellation: SharingCancellation | None,
    decision: SharingDecision | None,
) -> str:
    """Derive status from project state, which outranks any decision file.

    ``fulfilled`` wins over an earlier rejection: sharing is a project-level
    conversion, so once it happened the request's intent is satisfied however
    it got there. ``invalid`` marks the one inconsistency worth surfacing —
    an ``accepted`` record with a project that never migrated — because
    telling the requester that sharing completed would be a lie.
    """
    if project is None:
        return "orphaned"
    if project.sharing == "shared":
        return "fulfilled"
    if cancellation is not None:
        return "cancelled"
    if decision is not None and decision.decision == "rejected":
        return "rejected"
    if decision is not None and decision.decision == "accepted":
        return "invalid"
    if project.temporary:
        return "orphaned"
    if project.machine_id != request.observed_owner_machine_id:
        return "orphaned"
    return "pending"
