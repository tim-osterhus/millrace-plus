"""LAD intake policy and exclusive target ownership. No execution loop."""

from __future__ import annotations

import base64
import binascii
import fcntl
import hashlib
import math
import os
import re
import stat
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .subworkflow_v2 import Client

from .local_intake import Intake, IntakeError, canonical, fields, identity, strict_json

__all__ = [
    "Service",
    "validate_service",
    "validate_request",
    "claim_target",
    "target_lock",
    "materialize_exact",
    "validate_shared_target",
]

OWNER = ".millrace-plus-target-owner.json"
SERVICE = "millrace-plus-lad-service/v1"
SHA = re.compile(r"[0-9a-f]{64}\Z")
DECISIONS = {
    "retry-observation",
    "continue",
    "close-failed",
    "acknowledge-failed",
    "acknowledge-cancelled",
    "cancel-queued",
    "cancel-active",
    "settle",
    "settle-no-run",
}


def _string(value: object, *, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or "\0" in value:
        raise IntakeError("invalid_string")
    return value


def _sha(value: object) -> str:
    text = _string(value, maximum=64)
    if not SHA.fullmatch(text):
        raise IntakeError("invalid_sha256")
    return text


def _path(value: object, *, directory: bool = False) -> Path:
    path = Path(_string(value))
    if (
        not path.is_absolute()
        or str(path) != value
        or ".." in path.parts
        or path != path.resolve()
    ):
        raise IntakeError("noncanonical_path")
    for part in (path, *path.parents):
        if part.is_symlink():
            raise IntakeError("linked_path")
        if part.exists() and part != part.resolve():
            raise IntakeError("aliased_path")
        # samefile catches case aliases that resolve() can retain on macOS.
        if part.exists() and part.parent != part:
            for entry in part.parent.iterdir():
                if (
                    entry.name != part.name
                    and entry.name.casefold() == part.name.casefold()
                    and entry.samefile(part)
                ):
                    raise IntakeError("aliased_path")
    if directory and not path.is_dir():
        raise IntakeError("directory_missing")
    return path


def _filesystem_identity(path: Path) -> dict[str, object]:
    info = path.stat()
    return {"path": str(path), "device": info.st_dev, "inode": info.st_ino}


def _validate_identity(value: object, path: Path) -> dict[str, object]:
    obj = fields(value, {"path", "device", "inode"})
    if (
        obj["path"] != str(path)
        or type(obj["device"]) is not int
        or type(obj["inode"]) is not int
        or obj["device"] < 0
        or obj["inode"] < 1
    ):
        raise IntakeError("invalid_filesystem_identity")
    if obj != _filesystem_identity(path):
        raise IntakeError("filesystem_identity_changed")
    return obj


def _overlap(first: Path, second: Path) -> bool:
    if first == second or first in second.parents or second in first.parents:
        return True
    for left, right in ((first, second), (second, first)):
        if right.exists():
            for ancestor in (left, *left.parents):
                if ancestor.exists() and ancestor.samefile(right):
                    return True
    return False


def _definition(value: object) -> dict[str, object]:
    obj = fields(value, {"selector", "version", "sha256"})
    selector = _string(obj["selector"], maximum=256)
    if not re.fullmatch(r"(?:bundled|local):[A-Za-z][A-Za-z0-9_-]{0,63}", selector):
        raise IntakeError("invalid_definition")
    version = _string(obj["version"], maximum=256)
    digest = _sha(obj["sha256"])
    if selector.startswith("local:") and version != "sha256:" + digest:
        raise IntakeError("invalid_definition")
    return obj


def validate_service(value: object) -> dict[str, object]:
    """Validate frozen configuration without creating a parent or probing OS trust."""
    obj = fields(
        value,
        {
            "format",
            "service_id",
            "service_home",
            "target_root",
            "target_identity",
            "os_home",
            "os_profile",
            "worker_definition",
            "settings_sha256",
        },
    )
    if obj["format"] != SERVICE:
        raise IntakeError("invalid_service")
    identity(obj["service_id"])
    home = _path(obj["service_home"], directory=True)
    target = _path(obj["target_root"], directory=True)
    _validate_identity(obj["target_identity"], target)
    if _overlap(home, target):
        raise IntakeError("target_overlap")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", _string(obj["os_home"])):
        raise IntakeError("invalid_os_home")
    profile = fields(obj["os_profile"], {"path", "sha256"})
    _path(profile["path"])
    _sha(profile["sha256"])
    _definition(obj["worker_definition"])
    _sha(obj["settings_sha256"])
    return obj


def validate_request(payload: bytes) -> None:
    if not isinstance(payload, bytes) or not 1 <= len(payload) <= 65536:
        raise IntakeError("invalid_request_size")
    try:
        if not payload.decode("utf-8").strip():
            raise IntakeError("blank_request")
    except UnicodeError as exc:
        raise IntakeError("invalid_request_utf8") from exc


def _claim(value: dict[str, object]) -> None:
    obj = fields(
        value,
        {
            "task_id",
            "request_sha256",
            "definition",
            "call_key",
            "call_home",
            "output_ref",
            "create_request_id",
            "start_request_id",
        },
    )
    task_id = identity(obj["task_id"])
    _sha(obj["request_sha256"])
    _definition(obj["definition"])
    if obj["call_key"] != "task_" + task_id:
        raise IntakeError("invalid_claim")
    _path(obj["call_home"])
    if obj["output_ref"] != "results/" + task_id + ".json":
        raise IntakeError("invalid_claim")
    for name in ("create_request_id", "start_request_id"):
        identity(obj[name])


def _decision(value: dict[str, object]) -> None:
    obj = fields(value, {"task_id", "decision", "evidence"})
    identity(obj["task_id"])
    if obj["decision"] not in DECISIONS:
        raise IntakeError("invalid_decision")
    # Evidence is immutable local data at this layer, never accepted closure proof.
    canonical(obj["evidence"])
    if obj["decision"] == "cancel-queued" and obj["evidence"] is not None:
        raise IntakeError("invalid_decision")


def _read_exact(path: Path, *, limit: int = 1048576) -> bytes:
    _path(str(path))
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise IntakeError("linked_record")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise IntakeError("record_too_large")
        return raw
    finally:
        os.close(descriptor)


def _owner(config: dict[str, object], kind: str) -> dict[str, object]:
    home = _path(config["service_home"], directory=True)
    return {
        "format": "millrace-plus-target-owner/v1",
        "workflow_kind": kind,
        "service_id": config["service_id"],
        "service_home": str(home),
        "service_home_identity": _filesystem_identity(home),
    }


def _owner_check(
    value: object, config: dict[str, object], kind: str
) -> dict[str, object]:
    obj = fields(
        value,
        {
            "format",
            "workflow_kind",
            "service_id",
            "service_home",
            "service_home_identity",
        },
    )
    if obj != _owner(config, kind):
        raise IntakeError("target_owner_conflict")
    return obj


def _check_roots(config: dict[str, object], protected_roots: Sequence[Path]) -> Path:
    validate_service(config)
    target = _path(config["target_root"], directory=True)
    for root in protected_roots:
        if _overlap(target, _path(str(root))):
            raise IntakeError("target_overlap")
    return target


def claim_target(
    config: dict[str, object],
    *,
    protected_roots: Sequence[Path],
    kind: str = "lad",
) -> dict[str, object]:
    """Exclusively create the cooperative marker, preserving every existing file."""
    if kind not in {"lad", "cicd"}:
        raise IntakeError("invalid_workflow_kind")
    target = _check_roots(config, protected_roots)
    owner = _owner(config, kind)
    marker = target / OWNER
    raw = canonical(owner) + b"\n"
    try:
        descriptor = os.open(
            marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600
        )
    except FileExistsError:
        _owner_check(strict_json(_read_exact(marker)), config, kind)
        return owner
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        # Recheck identity after creation. A failed/partial marker is retained.
        _check_roots(config, protected_roots)
        directory = os.open(target, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.close(descriptor)
    return owner


@contextmanager
def target_lock(
    config: dict[str, object],
    *,
    protected_roots: Sequence[Path],
    kind: str = "lad",
) -> Iterator[None]:
    """Lock the exact owned inode briefly. Never infer writer closure from it."""
    target = _check_roots(config, protected_roots)
    marker = target / OWNER
    descriptor = os.open(marker, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise IntakeError("linked_owner")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise IntakeError("target_busy") from exc
        current = marker.stat(follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
            raise IntakeError("target_owner_changed")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            _owner_check(strict_json(stream.read(1048577)), config, kind)
        _check_roots(config, protected_roots)
        yield
    finally:
        os.close(descriptor)


def materialize_exact(path: Path, raw: bytes) -> None:
    """Create one immutable local task file, or verify an exact previous write."""
    _path(str(path))
    try:
        descriptor = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
    except FileExistsError:
        if _read_exact(path, limit=len(raw)) != raw:
            raise IntakeError("materialization_conflict")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.close(descriptor)


def _target_claim(
    value: object,
) -> tuple[dict[str, object], Service, dict[str, object]]:
    record = fields(
        value,
        {
            "format",
            "service_id",
            "task_id",
            "request_sha256",
            "target_root",
            "target_identity",
            "claim_sha256",
            "parent_instance_id",
            "parent_run_id",
            "worker_definition_sha256",
        },
    )
    if record["format"] != "millrace-lad-target/v1":
        raise IntakeError("invalid_lad_target")
    for name in ("service_id", "task_id", "parent_instance_id", "parent_run_id"):
        identity(record[name])
    for name in ("request_sha256", "claim_sha256", "worker_definition_sha256"):
        _sha(record[name])
    target = _path(record["target_root"], directory=True)
    _validate_identity(record["target_identity"], target)
    marker = fields(
        strict_json(_read_exact(target / OWNER)),
        {
            "format",
            "workflow_kind",
            "service_id",
            "service_home",
            "service_home_identity",
        },
    )
    service = Service(_path(marker.get("service_home"), directory=True))
    config = service.config
    _owner_check(marker, config, "lad")
    from . import catalog

    _check_roots(config, (service.home, service.home / "calls", *catalog.known_roots()))
    if (
        record["service_id"] != config["service_id"]
        or record["target_root"] != config["target_root"]
        or record["target_identity"] != config["target_identity"]
        or record["worker_definition_sha256"]
        != fields(config["worker_definition"], {"selector", "version", "sha256"})[
            "sha256"
        ]
    ):
        raise IntakeError("shared_target_conflict")
    active = [
        row
        for row in service.intake.tasks()
        if row["status"] in {"claimed", "running", "held"}
    ]
    if len(active) != 1 or active[0]["task_id"] != record["task_id"]:
        raise IntakeError("shared_target_inactive")
    task = active[0]
    claim = cast(dict[str, object], task["claim"])
    service._validate_claim(claim)
    if (
        record["request_sha256"] != task["request_sha256"]
        or record["request_sha256"] != claim["request_sha256"]
        or record["claim_sha256"] != hashlib.sha256(canonical(claim)).hexdigest()
    ):
        raise IntakeError("shared_claim_conflict")
    task_root = service.home / "tasks" / str(record["task_id"])
    if (
        _read_exact(task_root / "request.md", limit=65536) != task["request_bytes"]
        or _read_exact(task_root / "claim.json") != canonical(claim) + b"\n"
    ):
        raise IntakeError("shared_materialization_conflict")
    return record, service, task


def validate_shared_target(
    value: object,
    *,
    project: Path,
    bootstrap: bool = False,
    pid: int | None = None,
    script: Path | None = None,
    client: Client | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    """Validate current shared authority; only bootstrap correlates a live process."""
    from . import subworkflows
    from .subworkflow_records import (
        SubworkflowError,
        digest,
        mapping,
        public_request_digest,
    )
    from .subworkflow_v2 import SETTINGS
    from .workflow_os_client import WorkflowOSClient, load_profile, validate_receipt

    record, service, task = _target_claim(value)
    claim = mapping(task["claim"])
    project = _path(str(project), directory=True)
    persistent_runtime = None
    try:
        service.intake.lifecycle()
    except IntakeError as exc:
        if exc.code != "lifecycle_missing":
            raise
    else:
        from .lad_service_runtime import Runtime

        persistent_runtime = Runtime(service, client=client)
    call = (
        persistent_runtime.call(task)[1]
        if persistent_runtime is not None
        else subworkflows.inspect(Path(str(claim["call_home"])), str(claim["call_key"]))
    )
    request, paths = mapping(call["request"]), mapping(call["paths"])
    parent, inputs = mapping(request["parent"]), mapping(request["inputs"])
    if (
        call["format"]
        != (
            "millrace-plus-lad-subworkflow-call/v1"
            if persistent_runtime is not None
            else "millrace-plus-subworkflow-call/v2"
        )
        or call["claim_sha256"] != record["claim_sha256"]
        or call["definition"] != claim["definition"]
        or request["output_ref"] != claim["output_ref"]
        or call["os_home"] != service.config["os_home"]
        or call["os_profile"] != service.config["os_profile"]
        or paths["project"] != str(project)
        or parent.get("instance_id") != record["parent_instance_id"]
        or parent.get("run_id") != record["parent_run_id"]
        or inputs.get("lad_target") != record
        or inputs.get("request_markdown")
        != cast(bytes, task["request_bytes"]).decode("utf-8")
        or digest(call["settings"]) != service.config["settings_sha256"]
        or mapping(mapping(call["settings"])["host_paths"]).get("lad_target")
        != record["target_root"]
    ):
        raise IntakeError("shared_call_conflict")
    profile = load_profile(call["os_profile"], home=str(call["os_home"]))
    if profile["os_origin"] != call["os_origin"]:
        raise IntakeError("shared_profile_conflict")
    local = strict_json(_read_exact(project / "subworkflow-inputs.json"))
    if local != {
        "format": "millrace-plus-subworkflow-inputs/v1",
        "identity": {
            "call_home": claim["call_home"],
            "call_key": claim["call_key"],
            "output_root": request["output_root"],
        },
        "inputs": inputs,
    }:
        raise IntakeError("shared_manifest_conflict")
    manifest = project / "lad-target.json"
    if manifest.exists() or manifest.is_symlink():
        if _read_exact(manifest) != canonical(record) + b"\n":
            raise IntakeError("shared_manifest_conflict")
    elif not bootstrap:
        raise IntakeError("shared_manifest_missing")
    if not bootstrap:
        return record
    if _path(str(Path.cwd()), directory=True) != project / "foreground":
        raise IntakeError("bootstrap_cwd_conflict")
    if pid is None or script is None:
        raise IntakeError("bootstrap_identity_required")
    live_script = _path(str(script))
    _check_roots(service.config, (service.home, project, Path(str(paths["workflow"]))))
    deadline = clock() + 10
    connection = (
        persistent_runtime.client
        if persistent_runtime is not None
        else client
        or WorkflowOSClient(call["os_profile"], str(call["os_home"]), clock=clock)
    )
    sent = mapping(mapping(call["envelopes"])["start"])
    if (
        sent.get("request_id") != claim["start_request_id"]
        or mapping(call["reservations"])["start"] == "unsent"
        or sent.get("instance_id") != call["instance_id"]
    ):
        raise IntakeError("bootstrap_start_missing")
    while clock() < deadline:
        child = connection.read(str(call["instance_id"]), deadline=deadline)
        if (
            child.get("instance_id") != call["instance_id"]
            or child.get("project_root") != str(project)
            or child.get("workflow_file")
            != str(Path(str(paths["workflow"])) / "workflow.json")
            or any(
                child.get(name) != mapping(call["settings"])[name] for name in SETTINGS
            )
        ):
            raise IntakeError("bootstrap_child_conflict")
        controls = child.get("controls")
        if not isinstance(controls, list):
            raise IntakeError("bootstrap_receipt_missing")
        matches = [
            mapping(row)
            for row in controls
            if mapping(row).get("request_id") == claim["start_request_id"]
        ]
        if len(matches) != 1:
            raise IntakeError("bootstrap_receipt_missing")
        receipt = validate_receipt(matches[0], sent)
        if (
            receipt.get("status") != "applied"
            or receipt.get("run_id") is None
            or receipt.get("request_digest") != public_request_digest(sent)
            or receipt.get("instance_id") != call["instance_id"]
            or (call["run_id"] is not None and call["run_id"] != receipt["run_id"])
        ):
            raise IntakeError("bootstrap_receipt_conflict")
        run = connection.read(
            str(call["instance_id"]), str(receipt["run_id"]), deadline=deadline
        )
        if (
            run.get("id") != receipt["run_id"]
            or run.get("instance_id") != call["instance_id"]
            or run.get("start_request_id") != sent["request_id"]
        ):
            raise IntakeError("bootstrap_run_conflict")
        foreground = mapping(mapping(run.get("planes")).get("foreground"))
        active = foreground.get("active_invocation")
        candidates = [
            mapping(row)
            for row in run.get("invocations", [])
            if mapping(row).get("id") == active
        ]
        if len(candidates) != 1 or active not in run.get("inflight_invocations", []):
            raise IntakeError("bootstrap_invocation_conflict")
        invocation = candidates[0]
        launch = mapping(invocation.get("launch"))
        argv = invocation.get("argv")
        if (
            invocation.get("run_id") != receipt["run_id"]
            or invocation.get("plane_id") != "foreground"
            or invocation.get("node") != "init_session"
            or invocation.get("cwd") != str(project / "foreground")
            or not isinstance(argv, list)
            or len(argv) < 2
            or argv[1] != str(live_script)
            or launch.get("invocation_id") != active
            or launch.get("plane_id") != "foreground"
            or launch.get("argv") != argv
            or launch.get("adapter_id") != "command"
            or type(invocation.get("deadline")) not in {int, float}
            or not math.isfinite(float(invocation["deadline"]))
        ):
            raise IntakeError("bootstrap_invocation_conflict")
        # The public launch selects the actual captured control. Never derive a
        # Core home from the instance locator or mistake the workflow copy for it.
        captured_root = live_script.parent.parent
        if (
            live_script.name != "init_session.py"
            or live_script.parent.name != "scripts"
            or captured_root.name != "controls"
            or captured_root.parent.name != active
            or captured_root.parent.parent.name != "invocations"
        ):
            raise IntakeError("bootstrap_capture_path_conflict")
        capture = mapping(run.get("draft_revision"))
        revision = _sha(invocation.get("revision_id"))
        if (
            run.get("revision_id") != revision
            or run.get("startup_revision_id") != revision
            or capture.get("revision_id") != revision
            or capture.get("diagnostic") is not None
        ):
            raise IntakeError("bootstrap_capture_revision_conflict")
        encoded = mapping(capture.get("files")).get("scripts/init_session.py")
        if not isinstance(encoded, str) or len(encoded) > 1398104:
            raise IntakeError("bootstrap_capture_bytes_conflict")
        try:
            captured_bytes = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise IntakeError("bootstrap_capture_bytes_conflict") from exc
        if (
            len(captured_bytes) > 1048576
            or hashlib.sha256(_read_exact(live_script)).digest()
            != hashlib.sha256(captured_bytes).digest()
            or _read_exact(Path(str(paths["workflow"])) / "scripts/init_session.py")
            != captured_bytes
        ):
            raise IntakeError("bootstrap_capture_bytes_conflict")
        deadline = min(
            deadline, clock() + max(0, float(invocation["deadline"]) - time.time())
        )
        if clock() >= deadline:
            break
        if (
            invocation.get("status") == "active"
            and type(invocation.get("pid")) is int
            and invocation.get("pid") == pid
        ):
            parent_view = connection.read(
                str(record["parent_instance_id"]), deadline=deadline
            )
            parent_run = connection.read(
                str(record["parent_instance_id"]),
                str(record["parent_run_id"]),
                deadline=deadline,
            )
            if (
                parent_view.get("instance_id") != record["parent_instance_id"]
                or any(
                    parent_view.get(name) != mapping(call["settings"])[name]
                    for name in SETTINGS
                )
                or parent_run.get("id") != record["parent_run_id"]
                or parent_run.get("instance_id") != record["parent_instance_id"]
            ):
                raise IntakeError("bootstrap_parent_conflict")
            _check_roots(
                service.config,
                (
                    service.home,
                    project,
                    Path(str(paths["workflow"])),
                    _path(parent_view.get("project_root")),
                    _path(str(Path(str(parent_view.get("workflow_file"))).parent)),
                ),
            )
            return record
        if invocation.get("status") != "reserved" or invocation.get("pid") is not None:
            raise IntakeError("bootstrap_invocation_conflict")
        sleep(min(0.1, max(0, deadline - clock())))
    raise SubworkflowError("bootstrap_identity_deadline")


class Service:
    """Finite local operations over an already initialized, frozen service."""

    def __init__(self, home: Path) -> None:
        self.home = _path(str(home), directory=True)
        raw = _read_exact(self.home / "service.json")
        self.config = validate_service(strict_json(raw))
        if self.config["service_home"] != str(self.home):
            raise IntakeError("service_home_mismatch")
        self.service_id = str(self.config["service_id"])
        self.intake = Intake(
            self.home / "intake.sqlite3",
            self.service_id,
            validate_payload=validate_request,
            validate_claim=self._validate_claim,
            validate_decision=_decision,
            validate_decision_aftermath=self._decision_aftermath,
            task_id_for_key=self._task_id,
            decision_receipt=self._decision_receipt,
            authority_sha256=hashlib.sha256(raw).hexdigest(),
        )

    def _task_id(self, key: str) -> str:
        return uuid.uuid5(uuid.UUID(hex=self.service_id), "lad-task/" + key).hex

    def _validate_claim(self, value: dict[str, object]) -> None:
        _claim(value)
        if value["definition"] != self.config["worker_definition"] or value[
            "call_home"
        ] != str(self.home / "calls"):
            raise IntakeError("claim_conflict")
        # Keep the existing public subworkflow v1 namespace/derivation frozen.
        namespace = uuid.UUID("d0ad2e51-b41f-5a75-886a-a17623fb5db4")
        for key, operation in (
            ("create_request_id", "create_instance"),
            ("start_request_id", "start_run"),
        ):
            name = (
                str(value["call_home"]) + "/" + str(value["call_key"]) + "/" + operation
            )
            if value[key] != uuid.uuid5(namespace, name).hex:
                raise IntakeError("claim_conflict")

    def _decision_receipt(
        self, request_id: str, intent: dict[str, object]
    ) -> dict[str, object]:
        from .lad_service_disposition import receipt

        return receipt(self, request_id, intent)

    def _decision_aftermath(
        self,
        task: dict[str, object],
        intent: dict[str, object],
        receipt: dict[str, object],
    ) -> None:
        from .lad_service_disposition import aftermath

        aftermath(self, task, intent, receipt)

    def submit(self, key: str, request_file: Path) -> dict[str, object]:
        payload = _read_exact(request_file, limit=65536)
        validate_request(payload)
        from .lad_service_runtime import preflight_submit, wake

        preflight_submit(self, payload)
        with target_lock(self.config, protected_roots=(self.home,)):
            result = self.intake.submit(key, payload)
        wake(self)
        return result

    def status(self, task_id: str | None = None) -> dict[str, object]:
        tasks = self.intake.tasks(task_id)
        # JSON carries decoded text while storage preserves its exact UTF-8 bytes.
        return {
            "service_id": self.service_id,
            "tasks": [
                row
                | {"request_bytes": cast(bytes, row["request_bytes"]).decode("utf-8")}
                for row in tasks
            ],
        }

    def cancel(self, task_id: str, request_id: str) -> dict[str, object]:
        task = self.intake.tasks(identity(task_id))[0]
        if task["claim"] is not None:
            from .lad_service_runtime import Runtime

            return Runtime(self).cancel(task_id, request_id)
        intent: dict[str, object] = {
            "task_id": identity(task_id),
            "decision": "cancel-queued",
            "evidence": None,
        }
        with target_lock(self.config, protected_roots=(self.home,)):
            return self.intake.decide(
                request_id, intent, check=lambda _: None, cancel_queued=True
            )

    def claim_next(
        self,
        *,
        writer_guard: Callable[[], None],
        _deadline: float | None = None,
        _clock: Callable[[], float] = time.monotonic,
        _sleep: Callable[[float], None] = time.sleep,
    ) -> dict[str, object] | None:
        """Internal claim; only target mutex acquisition may wait within its node."""
        while True:
            if _deadline is not None and _clock() >= _deadline:
                raise IntakeError("target_busy")
            acquired = False
            try:
                with target_lock(self.config, protected_roots=(self.home,)):
                    acquired = True
                    if _deadline is not None and _clock() >= _deadline:
                        raise IntakeError("target_busy")
                    # The mutex proves no writer closure. Guard/transaction errors
                    # after acquisition are never retried, even with this code.
                    writer_guard()
                    if _deadline is not None and _clock() >= _deadline:
                        raise IntakeError("target_busy")
                    return self.intake.claim(self._build_claim)
            except IntakeError as exc:
                if acquired or exc.code != "target_busy" or _deadline is None:
                    raise
                remaining = _deadline - _clock()
                if remaining <= 0:
                    raise
                _sleep(min(0.05, remaining))

    def _build_claim(self, task: dict[str, object]) -> dict[str, object]:
        task_id = str(task["task_id"])
        call_home, call_key = str(self.home / "calls"), "task_" + task_id
        namespace = uuid.UUID("d0ad2e51-b41f-5a75-886a-a17623fb5db4")
        claim: dict[str, object] = {
            "task_id": task_id,
            "request_sha256": task["request_sha256"],
            "definition": self.config["worker_definition"],
            "call_key": call_key,
            "call_home": call_home,
            "output_ref": "results/" + task_id + ".json",
        }
        for key, operation in (
            ("create_request_id", "create_instance"),
            ("start_request_id", "start_run"),
        ):
            claim[key] = uuid.uuid5(
                namespace, call_home + "/" + call_key + "/" + operation
            ).hex
        return claim

    def materialize_task(self, task_id: str) -> None:
        """Replay immutable local request/claim files. Do not initialize a child."""
        with target_lock(self.config, protected_roots=(self.home,)):
            task = self.intake.tasks(identity(task_id))[0]
            if (
                task["status"] not in {"claimed", "running", "held"}
                or task["claim"] is None
            ):
                raise IntakeError("task_not_active")
            root = self.home / "tasks" / task_id
            _path(str(root))
            root.mkdir(parents=True, exist_ok=True)
            materialize_exact(root / "request.md", cast(bytes, task["request_bytes"]))
            materialize_exact(root / "claim.json", canonical(task["claim"]) + b"\n")

    def advance_task(
        self,
        task_id: str,
        request_file: Path,
        *,
        cwd: Path,
        client: Client | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> dict[str, object]:
        """Advance one already claimed task; never claim, settle or loop a queue."""
        from .subworkflow_records import digest, mapping
        from .subworkflow_v2 import SETTINGS, _advance_bytes
        from .subworkflow_v2_records import parse_request
        from .workflow_os_client import WorkflowOSClient, load_profile

        raw = _read_exact(request_file)
        request = parse_request(raw, cwd=cwd)
        target, service, task = _target_claim(
            mapping(request["inputs"]).get("lad_target")
        )
        claim = mapping(task["claim"])
        if (
            service.home != self.home
            or task["task_id"] != identity(task_id)
            or request["call_home"] != claim["call_home"]
            or request["call_key"] != claim["call_key"]
            or request["definition"] != claim["definition"]
            or request["output_ref"] != claim["output_ref"]
            or request["os_profile"] != self.config["os_profile"]
            or mapping(request["parent"]).get("instance_id")
            != target["parent_instance_id"]
            or mapping(request["parent"]).get("run_id") != target["parent_run_id"]
            or mapping(request["inputs"]).get("request_markdown")
            != cast(bytes, task["request_bytes"]).decode("utf-8")
        ):
            raise IntakeError("shared_call_conflict")
        profile = load_profile(request["os_profile"], home=str(self.config["os_home"]))
        connection = client or WorkflowOSClient(
            request["os_profile"], str(profile["os_home"]), clock=clock
        )
        parent = connection.read(
            str(target["parent_instance_id"]), deadline=clock() + 2
        )
        settings = {key: parent[key] for key in SETTINGS}
        if (
            digest(settings) != self.config["settings_sha256"]
            or mapping(settings["host_paths"]).get("lad_target")
            != target["target_root"]
        ):
            raise IntakeError("shared_settings_conflict")
        _check_roots(
            self.config,
            (
                self.home,
                Path(str(parent["project_root"])),
                Path(str(parent["workflow_file"])).parent,
            ),
        )
        return _advance_bytes(
            raw,
            expected_home=str(self.config["os_home"]),
            claim_sha256=str(target["claim_sha256"]),
            cwd=cwd,
            client=connection,
            clock=clock,
            sleep=sleep,
        )
