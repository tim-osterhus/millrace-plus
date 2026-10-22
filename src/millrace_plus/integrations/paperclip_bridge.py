"""One immutable Paperclip assignment observed through existing OS public controls."""

from __future__ import annotations

import importlib.metadata
import os
import signal
import stat
import threading
import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import cast

from millrace_plus import catalog
from millrace_plus.subworkflow_records import SubworkflowError, atomic_json
from millrace_plus.subworkflows import closed

from .paperclip_http import OSClient
from .paperclip_records import (
    BridgeError,
    Store,
    date,
    digest,
    freeze,
    identifier,
    load_json,
    mapping,
    now,
    overlaps,
    parse_assignment,
    parse_config,
    request,
    request_digest,
    safe_path,
    text,
    validate_record,
)

__all__ = ["run", "inspect", "EXIT_CODES"]
EXIT_CODES = {
    "succeeded": 0,
    "failed": 1,
    "cancelled": 3,
    "attention_required": 4,
    "refused": 5,
    "uncertain": 6,
}


def _inputs(config_file: Path, assignment_file: Path):
    with config_file.open("rb") as stream:
        config = parse_config(stream.read(65537))
    with assignment_file.open("rb") as stream:
        context = parse_assignment(stream.read(65537))
    return config, context, freeze(config, context)


def _definition(assignment: dict[str, object]) -> Path:
    config = mapping(assignment["configuration"])
    selected = mapping(config["definition"])
    if str(selected["selector"]).startswith("bundled:") and selected[
        "version"
    ] != importlib.metadata.version("millrace-plus"):
        raise BridgeError("definition_mismatch")
    for row in catalog.list_templates():
        if row["selector"] == selected["selector"]:
            source = Path(row["path"])
            if catalog.workflow_fingerprint(source) != selected["sha256"]:
                raise BridgeError("definition_mismatch")
            for root in (config["destination_root"], config["binding_state_root"]):
                if overlaps(source, Path(str(root))):
                    raise BridgeError("definition_root_overlap")
            return source
    raise BridgeError("definition_missing")


def _result(assignment, context, status, diagnostic=None, saved=None):
    saved = saved or {}
    observed = mapping(saved.get("observation", {}))
    config = mapping(assignment["configuration"])
    return {
        "format": "millrace-paperclip-result/v1",
        "assignment": assignment["identity"],
        "assignment_sha256": digest(assignment),
        "binding_digest": assignment["binding_digest"],
        "attempt_id": context["attempt_id"],
        "agent_id": context["agent_id"],
        "os_home": config["os_home"],
        "instance_id": saved.get("instance_id"),
        "run_id": saved.get("run_id"),
        "graph_revision": observed.get("graph_revision"),
        "status": status,
        "terminal_outcome": observed.get("terminal_outcome"),
        "summary": diagnostic or ("Graph " + status),
        "diagnostic": diagnostic,
        "result_root": mapping(assignment["paths"])["root"],
        "refs": observed.get("refs", []) if status == "succeeded" else [],
    }


def _save(store: Store, saved: dict[str, object], **changes: object) -> None:
    saved.update(changes)
    store.save(saved)


def _binding_at(home: Path, root: Path) -> dict[str, object]:
    # A record's own identity cannot select the directory used to validate it.
    path = safe_path(str(root / "call.json"))
    try:
        with path.open("rb") as stream:
            value = load_json(stream.read(1048577), 1048576)
        return validate_record(value, home, root.name)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise BridgeError("invalid_binding") from exc


def _settled_prior(store: Store, client: OSClient) -> list[tuple[Path, str]]:
    """Read old exact targets outside the short reservation lock."""
    proofs = []
    if not store.home.exists():
        return proofs
    identity = mapping(store.assignment["identity"])
    for root in store.home.iterdir():
        if not root.is_dir():
            continue
        old = _binding_at(store.home, root)
        if root.name == store.key:
            continue
        path = root / "call.json"
        previous = mapping(old["assignment"])
        previous_id = mapping(previous.get("identity"))
        if any(
            previous_id.get(k) != identity[k]
            for k in ("paperclip_origin", "company_id", "issue_id")
        ):
            continue
        if old["phase"] == "cleanup_required" or old["cleanup_required"]:
            raise BridgeError("prior_binding_unsettled")
        old_client = OSClient(mapping(previous["configuration"]))
        if old["run_id"] is not None:
            # Even a local terminal flag cannot grant a new revision.
            view = old_client.read(old["instance_id"], old["run_id"])
            if not closed(view):
                raise BridgeError("prior_binding_unsettled")
        elif old["phase"] != "terminal":
            raise BridgeError("prior_binding_unsettled")
        elif old["instance_id"] is not None:
            view = old_client.read(old["instance_id"])
            if view.get("runs") != [] or mapping(old["envelopes"])["start"] is not None:
                raise BridgeError("prior_binding_unsettled")
        proofs.append((path, str(old["record_sha256"])))
    return proofs


def _reserve(
    assignment: dict[str, object], client: OSClient
) -> tuple[Store, dict[str, object]]:
    store = Store(assignment)
    if store.path.exists():
        saved = store.load()
        if saved["assignment"] != assignment:
            raise BridgeError("assignment_conflict")
        return store, saved
    _definition(assignment)
    config, paths = mapping(assignment["configuration"]), mapping(assignment["paths"])
    for name in ("destination_root", "binding_state_root"):
        root = safe_path(config[name])
        if root.exists() and (not root.is_dir() or root.stat().st_uid != os.getuid()):
            raise BridgeError("root_not_owned")
        catalog.check_ownership(root / "workflow.json", root)
    proofs = _settled_prior(store, client)
    with store.lock():
        if store.path.exists():
            saved = store.load()
            if saved["assignment"] != assignment:
                raise BridgeError("assignment_conflict")
            return store, saved
        # A racing issue reservation invalidates the pre-lock read proof.
        expected = {path.parent for path, _ in proofs}
        related = set()
        for root in store.home.iterdir():
            if root.is_dir():
                value = _binding_at(store.home, root)
                if root.name == store.key:
                    continue
                key = mapping(mapping(value.get("assignment")).get("identity"))
                if all(
                    key.get(k) == mapping(assignment["identity"])[k]
                    for k in ("paperclip_origin", "company_id", "issue_id")
                ):
                    related.add(root)
        if related != expected or any(
            _binding_at(store.home, path.parent)["record_sha256"] != checksum
            for path, checksum in proofs
        ):
            raise BridgeError("prior_binding_changed")
        if os.path.lexists(store.root) or os.path.lexists(str(paths["root"])):
            raise BridgeError("binding_root_not_owned")
        created = now()
        saved = {
            "format": "millrace-paperclip-call/v1",
            "assignment": assignment,
            "assignment_sha256": digest(assignment),
            "created_at": created,
            "deadline_at": (
                date(created) + timedelta(seconds=cast(int, config["wait_seconds"]))
            ).isoformat(),
            "cleanup_deadline_at": None,
            "cancelled_at": None,
            "cleanup_required": False,
            "envelopes": {
                "create": request(assignment, "create"),
                "start": None,
                "cancel": None,
            },
            "receipts": {},
            "instance_id": None,
            "run_id": None,
            "observation": {},
            "phase": "prepared",
            "diagnostic": None,
            "cancel_intent": False,
            "copy_prepared": False,
        }
        saved["authority_sha256"] = digest(
            {k: saved[k] for k in ("assignment", "created_at", "deadline_at")}
        )
        store.root.mkdir(mode=0o700)
        store.save(saved)
        return store, saved


def _copy(store: Store, saved: dict[str, object]) -> None:
    assignment = mapping(saved["assignment"])
    paths, config = mapping(assignment["paths"]), mapping(assignment["configuration"])
    root, workflow, project = (
        safe_path(paths[k]) for k in ("root", "workflow", "project")
    )
    if saved["copy_prepared"]:
        if (
            catalog.workflow_fingerprint(workflow)
            != mapping(config["definition"])["sha256"]
        ):
            raise BridgeError("definition_mismatch")
        return
    if root.exists():
        # A crash during copying cannot confer ownership of arbitrary leftovers.
        raise BridgeError("copy_requires_inspection")
    _definition(assignment)
    root.mkdir(mode=0o700, parents=True)
    copied = catalog.init_template(
        str(mapping(config["definition"])["selector"]), workflow
    )
    if (
        mapping(copied["provenance"])["sha256"]
        != mapping(config["definition"])["sha256"]
    ):
        raise BridgeError("definition_mismatch")
    project.mkdir(mode=0o700)
    graph = mapping(load_json((workflow / "workflow.json").read_bytes(), 1048576))
    for plane in mapping(graph.get("planes")).values():
        workspace = Path(text(mapping(plane).get("workspace")))
        target = safe_path(str(project / workspace))
        if target != project and project not in target.parents:
            raise BridgeError("invalid_workspace")
        target.mkdir(mode=0o700, parents=True, exist_ok=True)
    atomic_json(
        project / "subworkflow-inputs.json",
        {
            "format": "millrace-plus-subworkflow-inputs/v1",
            "identity": assignment["identity"],
            "inputs": {"request_markdown": assignment["description"]},
        },
        limit=73728,
    )
    _save(store, saved, copy_prepared=True)


def _receipt(store: Store, saved: dict[str, object], name: str, value: object) -> None:
    row = mapping(value)
    envelope = mapping(mapping(saved["envelopes"])[name])
    if (
        row.get("request_id") != envelope["request_id"]
        or row.get("operation") != envelope["operation"]
        or row.get("request_digest") != request_digest(envelope)
        or row.get("status") not in {"applied", "queued", "refused"}
    ):
        raise BridgeError("invalid_os_receipt")
    mapping(saved["receipts"])[name] = row
    if row["status"] == "applied":
        if name == "create":
            saved["instance_id"] = identifier(row.get("instance_id"))
            saved["phase"] = "created"
        elif name == "start":
            saved["run_id"] = identifier(row.get("run_id"))
            saved["phase"] = "observing"
    store.save(saved)


def _observation(
    store: Store, saved: dict[str, object], name: str, value: dict[str, object]
) -> None:
    envelope = mapping(mapping(saved["envelopes"])[name])
    if (
        value.get("contract") != "millrace-os-router-call/v1"
        or value.get("home")
        != mapping(mapping(saved["assignment"])["configuration"])["os_home"]
        or value.get("request_id") != envelope["request_id"]
        or value.get("request_digest") != request_digest(envelope)
        or value.get("source") not in {"os_owned_call", "core_receipt"}
        or value.get("phase")
        not in {
            "admission_pending",
            "call_active",
            "call_returned",
            "call_failed",
            "receipt_observed",
        }
    ):
        raise BridgeError("invalid_os_observation")
    if value.get("core_receipt") is not None:
        _receipt(store, saved, name, value["core_receipt"])


def _dispatch(
    store: Store, saved: dict[str, object], client: OSClient, name: str
) -> None:
    _save(
        store,
        saved,
        cleanup_required=True,
        phase={"create": "creating", "start": "starting", "cancel": "cancel_requested"}[
            name
        ],
    )
    _observation(
        store, saved, name, client.submit(mapping(mapping(saved["envelopes"])[name]))
    )


def _refresh(
    store: Store,
    saved: dict[str, object],
    client: OSClient,
    *,
    active_summary: bool = False,
) -> dict[str, object] | None:
    if saved["instance_id"] is None:
        return None
    previous = mapping(saved["observation"])
    full_required = (
        not active_summary
        or saved["cancel_intent"]
        or saved["phase"] == "cleanup_required"
        or (
            bool(previous)
            and (
                previous.get("id") != saved["run_id"]
                or previous.get("status") not in {"ready", "running", "waiting"}
            )
        )
        or any(
            mapping(row).get("writer_state") in {"unknown", "may_live"}
            for row in previous.get("invocations", [])
        )
        or any(
            mapping(row).get("effective_writer") in {"unknown", "may_live"}
            for row in previous.get("completions", [])
        )
    )
    instance = client.read(saved["instance_id"])
    for name in ("start", "cancel"):
        envelope = mapping(saved["envelopes"])[name]
        if envelope is not None:
            for receipt in cast(list[dict[str, object]], instance.get("controls", [])):
                if receipt.get("request_id") == mapping(envelope)["request_id"]:
                    _receipt(store, saved, name, receipt)
    if saved["run_id"] is None:
        return None
    rows = instance.get("runs")
    if (
        not full_required
        and isinstance(rows, list)
        and all(isinstance(row, dict) for row in rows)
    ):
        selected = [row for row in rows if row.get("id") == saved["run_id"]]
        if len(selected) == 1:
            summary = selected[0]
            if (
                isinstance(summary.get("status"), str)
                and summary["status"] in {"ready", "running", "waiting"}
                and "pause_reason" in summary
                and summary["pause_reason"] is None
                and (
                    not previous
                    or previous.get("graph_revision") == summary.get("revision_id")
                )
            ):
                # This only defers history. It cannot replace prior full writer facts.
                return None
    view = client.read(saved["instance_id"], saved["run_id"])
    required = {
        "id",
        "status",
        "state_version",
        "quiescent",
        "inflight_invocations",
        "invocations",
        "completions",
        "revision_id",
    }
    if (
        not required <= view.keys()
        or any(
            not isinstance(view[name], list)
            for name in ("inflight_invocations", "invocations", "completions")
        )
        or any(
            not isinstance(row, dict)
            or not {"id", "status", "writer_state"} <= row.keys()
            for row in view["invocations"]
        )
        or any(
            not isinstance(row, dict)
            or not {"invocation_id", "effective_writer"} <= row.keys()
            for row in view["completions"]
        )
    ):
        raise BridgeError("invalid_os_read")
    for receipt in cast(list[dict[str, object]], view.get("controls", [])):
        envelope = mapping(saved["envelopes"])["cancel"]
        if (
            envelope is not None
            and receipt.get("request_id") == mapping(envelope)["request_id"]
        ):
            _receipt(store, saved, "cancel", receipt)
    projection = {
        k: view[k]
        for k in ("id", "status", "state_version", "quiescent", "inflight_invocations")
    }
    projection.update(
        {
            "invocations": [
                {k: row[k] for k in ("id", "status", "writer_state")}
                for row in cast(list[dict[str, object]], view["invocations"])
            ],
            "completions": [
                {k: row[k] for k in ("invocation_id", "effective_writer")}
                for row in cast(list[dict[str, object]], view["completions"])
            ],
            "graph_revision": view["revision_id"],
            "terminal_outcome": None,
            "refs": [],
        }
    )
    if closed(view):
        graph = mapping(
            load_json(
                (
                    Path(
                        str(mapping(mapping(saved["assignment"])["paths"])["workflow"])
                    )
                    / "workflow.json"
                ).read_bytes(),
                1048576,
            )
        )
        refs = []
        terminals = {
            row.get("completion_id")
            for row in cast(list[dict[str, object]], view.get("transitions", []))
            if "finish" in mapping(load_json(str(row.get("action")).encode()))
        }
        terminal_outcomes = set()
        for completion in cast(list[dict[str, object]], view["completions"]):
            if completion.get("classification") != "semantic":
                continue
            result = mapping(load_json(str(completion["stdout"]).strip().encode()))
            if completion["invocation_id"] in terminals:
                terminal_outcomes.add(result.get("outcome"))
            workspace = str(
                mapping(mapping(graph["planes"])[str(completion["plane_id"])])[
                    "workspace"
                ]
            )
            raw_refs = result.get("refs", [])
            if not isinstance(raw_refs, list):
                raise BridgeError("invalid_artifact_ref")
            for ref in raw_refs:
                relative = text(ref)
                if (
                    Path(relative).is_absolute()
                    or "\\" in relative
                    or any(p in {"", ".", ".."} for p in relative.split("/"))
                ):
                    raise BridgeError("invalid_artifact_ref")
                path = str(Path("project") / workspace / relative)
                if path not in refs:
                    refs.append(path)
        if len(refs) > 32:
            raise BridgeError("invalid_artifact_ref")
        projection["refs"] = refs
        if len(terminal_outcomes) == 1:
            projection["terminal_outcome"] = terminal_outcomes.pop()
    _save(store, saved, observation=projection)
    return view


def _validate_refs(saved: dict[str, object]) -> None:
    root = safe_path(mapping(mapping(saved["assignment"])["paths"])["root"])
    for ref in cast(list[object], mapping(saved["observation"])["refs"]):
        try:
            target = safe_path(str(root / text(ref)))
            if root not in target.parents or not stat.S_ISREG(target.lstat().st_mode):
                raise BridgeError("invalid_artifact_ref")
        except (OSError, SubworkflowError, BridgeError) as exc:
            raise BridgeError("invalid_artifact_ref") from exc


def _cancel_intent(store: Store, saved: dict[str, object]) -> None:
    if not saved["cancel_intent"]:
        config = mapping(mapping(saved["assignment"])["configuration"])
        started = now()
        _save(
            store,
            saved,
            cancel_intent=True,
            cancelled_at=started,
            cleanup_deadline_at=(
                date(started) + timedelta(seconds=cast(int, config["cleanup_seconds"]))
            ).isoformat(),
        )


def _wait_for_observation(saved, cancelled):
    end = time.monotonic() + 1.0
    deadline = (
        saved["cleanup_deadline_at"] if saved["cancel_intent"] else saved["deadline_at"]
    )
    while True:
        if not saved["cancel_intent"] and cancelled():
            return
        remaining = min(
            end - time.monotonic(), (date(deadline) - date(now())).total_seconds()
        )
        if remaining <= 0:
            return
        time.sleep(min(0.05, remaining))


def _observe(store, saved, client, context, cancelled):
    assignment = mapping(saved["assignment"])
    # The first deadline remains authoritative on every retry and process restart.
    while True:
        targets = saved["instance_id"], saved["run_id"]
        if cancelled() or date(saved["deadline_at"]) <= date(now()):
            _cancel_intent(store, saved)
        try:
            view = _refresh(store, saved, client, active_summary=True)
            if view is not None and closed(view):
                status = str(view["status"])
                _save(
                    store,
                    saved,
                    phase="terminal",
                    diagnostic=None,
                    cleanup_required=False,
                )
                if status == "succeeded":
                    _validate_refs(saved)
                return _result(assignment, context, status, saved=saved)
            if (
                view is not None
                and view.get("status") == "paused"
                and not saved["cancel_intent"]
            ):
                status = (
                    "attention_required"
                    if closed(view, attention=True)
                    else "uncertain"
                )
                _save(
                    store,
                    saved,
                    phase="attention_required"
                    if status == "attention_required"
                    else "cleanup_required",
                    diagnostic="graph_paused",
                )
                return _result(assignment, context, status, "graph_paused", saved)
            envs = mapping(saved["envelopes"])
            receipts = mapping(saved["receipts"])
            if any(mapping(v).get("status") == "refused" for v in receipts.values()):
                create = mapping(receipts.get("create", {}))
                no_target_refusal = (
                    {"instance_id", "run_id", "plane_id"} <= create.keys()
                    and create.get("status") == "refused"
                    and type(create.get("state_version")) is int
                    and create["state_version"] == 0
                    and create.get("instance_id") is None
                    and create.get("run_id") is None
                    and create.get("plane_id") is None
                    and isinstance(create.get("code"), str)
                    and bool(create["code"])
                    and create.get("retryable") is False
                    and saved["instance_id"] is None
                    and saved["run_id"] is None
                    and envs["start"] is None
                    and envs["cancel"] is None
                )
                _save(
                    store,
                    saved,
                    phase="terminal" if saved["run_id"] is None else "cleanup_required",
                    diagnostic="os_request_refused",
                    cleanup_required=False
                    if no_target_refusal
                    else saved["cleanup_required"],
                )
                return _result(
                    assignment, context, "refused", "os_request_refused", saved
                )
            if saved["instance_id"] is None:
                if saved["cancel_intent"] and saved["phase"] == "prepared":
                    _save(store, saved, phase="terminal")
                    return _result(
                        assignment,
                        context,
                        "cancelled",
                        "cancelled_before_create",
                        saved,
                    )
                # Recovery replays the saved create POST, also after OS restart.
                _dispatch(store, saved, client, "create")
            elif envs["start"] is None:
                if cancelled():
                    _cancel_intent(store, saved)
                if saved["cancel_intent"]:
                    _save(store, saved, phase="terminal", cleanup_required=False)
                    return _result(
                        assignment,
                        context,
                        "cancelled",
                        "cancelled_before_start",
                        saved,
                    )
                envs["start"] = request(assignment, "start", saved["instance_id"])
                _dispatch(store, saved, client, "start")
            elif saved["run_id"] is None:
                # A lost start reply is recovered only from the original saved receipt.
                _observation(
                    store,
                    saved,
                    "start",
                    client.observe(mapping(envs["start"]), saved["instance_id"], None),
                )
            elif saved["cancel_intent"]:
                if envs["cancel"] is None:
                    envs["cancel"] = request(
                        assignment, "cancel", saved["instance_id"], saved["run_id"]
                    )
                _dispatch(store, saved, client, "cancel")
        except BridgeError as exc:
            if exc.code in {
                "invalid_os_receipt",
                "invalid_os_observation",
                "invalid_os_read",
                "wrong_os_target",
                "invalid_artifact_ref",
            }:
                _save(store, saved, phase="cleanup_required", diagnostic=exc.code)
                return _result(
                    assignment,
                    context,
                    "refused" if exc.code == "invalid_artifact_ref" else "uncertain",
                    exc.code,
                    saved,
                )
            _save(store, saved, diagnostic=exc.code)
            if exc.code == "os_http_401":
                _save(store, saved, phase="cleanup_required")
                return _result(assignment, context, "uncertain", exc.code, saved)
        except (KeyError, TypeError, ValueError, SubworkflowError):
            _save(store, saved, phase="cleanup_required", diagnostic="invalid_os_read")
            return _result(assignment, context, "uncertain", "invalid_os_read", saved)
        if saved["cleanup_deadline_at"] is not None and date(
            saved["cleanup_deadline_at"]
        ) <= date(now()):
            _save(store, saved, phase="cleanup_required", diagnostic="cleanup_deadline")
            return _result(assignment, context, "uncertain", "cleanup_deadline", saved)
        if targets != (saved["instance_id"], saved["run_id"]):
            continue  # Finite create/start identity progress needs no polling delay.
        _wait_for_observation(saved, cancelled)


def inspect(config_file: Path, assignment_file: Path) -> dict[str, object]:
    """Read a strict retained binding without dispatch or observer ownership."""
    _, _, assignment = _inputs(config_file, assignment_file)
    return Store(assignment).load()


def run(
    config_file: Path,
    assignment_file: Path,
    *,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, object]:
    """Reserve once, attach duplicate attempts, and return only observed graph truth.

    Signals request public cancellation. They never equate helper exit with closure.
    The caller can inject a cancellation predicate in an owned test thread.
    """
    config, context, assignment = _inputs(config_file, assignment_file)
    client, store, saved = OSClient(config), Store(assignment), None
    event = threading.Event()
    previous = {}
    if cancelled is None and threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous[sig] = signal.signal(sig, lambda *_: event.set())
    try:
        store, saved = _reserve(assignment, client)
        with store.lock(observer=True):
            # Reload after acquiring ownership; a duplicate cannot extend deadlines.
            saved = store.load()
            if not saved["cancel_intent"]:
                _copy(store, saved)
            return _observe(store, saved, client, context, cancelled or event.is_set)
    except (BridgeError, catalog.CatalogError, SubworkflowError) as exc:
        if store.path.exists():
            try:
                saved = store.load()
                assignment = mapping(saved["assignment"])
            except (BridgeError, SubworkflowError, OSError):
                saved = None
        return _result(
            assignment,
            context,
            "attention_required" if exc.code == "attention_busy" else "refused",
            exc.code,
            saved,
        )
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
