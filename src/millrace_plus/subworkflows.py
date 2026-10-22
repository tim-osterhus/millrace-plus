"""Finite reusable child calls over the public Plus controls, without a service."""

from __future__ import annotations

import importlib.metadata
import os
import signal
import threading
import time
from datetime import timedelta
from pathlib import Path
from typing import cast

from . import catalog
from .subworkflow_process import ControlProcess, alive
from .subworkflow_records import (
    JOURNAL_FORMAT,
    Journal,
    SubworkflowError,
    _date,
    atomic_json,
    canonical,
    canonical_path,
    digest,
    envelope,
    mapping,
    overlaps,
    parse_request,
    public_request_digest,
    request_id,
    seconds_left,
    timestamp,
)

__all__ = ["prepare", "call", "inspect", "cancel", "reconcile", "closed"]
TERMINAL = {"succeeded", "failed", "cancelled"}
OUTCOMES = {
    "succeeded": "CHILD_SUCCEEDED",
    "failed": "CHILD_FAILED",
    "cancelled": "CHILD_CANCELLED",
}


def _control():
    from .router import control

    return control


def _read_request(file: Path) -> dict[str, object]:
    try:
        with file.open("rb") as stream:
            return parse_request(stream.read(65537), cwd=Path.cwd())
    except OSError as exc:
        raise SubworkflowError("request_unreadable") from exc


def _parent(request: dict[str, object]) -> dict[str, object]:
    parent = mapping(request["parent"])
    view = _control().inspect_instance(
        str(parent["instance_id"]), state_home=Path(str(parent["state_home"]))
    )
    root = canonical_path(view["project_root"])
    output = canonical_path(request["output_root"])
    if output != root and root not in output.parents:
        raise SubworkflowError("output_outside_parent")
    home = canonical_path(request["call_home"])
    for source in (
        root,
        canonical_path(parent["state_home"]),
        canonical_path(str(Path(str(view["workflow_file"])).parent)),
    ):
        if overlaps(home, source):
            raise SubworkflowError("parent_child_overlap")
    # The optional run locator is correlation, but must identify a real parent run.
    if "run_id" in parent:
        run = _control().inspect_run(
            str(parent["instance_id"]),
            str(parent["run_id"]),
            state_home=Path(str(parent["state_home"])),
        )
        if "plane_id" in parent and str(parent["plane_id"]) not in mapping(
            run["planes"]
        ):
            raise SubworkflowError("parent_plane_missing")
    try:
        catalog.check_ownership(home / "workflow.json", home)
    except catalog.CatalogError as exc:
        raise SubworkflowError(exc.code) from exc
    return view


def _definition(request: dict[str, object]) -> Path:
    selected = mapping(request["definition"])
    if str(selected["selector"]).startswith("bundled:") and selected[
        "version"
    ] != importlib.metadata.version("millrace-plus"):
        raise SubworkflowError("definition_mismatch")
    try:
        for row in catalog.list_templates():
            if row["selector"] == selected["selector"]:
                source = Path(row["path"])
                if catalog.workflow_fingerprint(source) != selected["sha256"]:
                    raise SubworkflowError("definition_mismatch")
                return source
    except catalog.CatalogError as exc:
        raise SubworkflowError(exc.code) from exc
    raise SubworkflowError("definition_missing")


def _authority(record: dict[str, object]) -> dict[str, object]:
    return {
        k: record[k]
        for k in (
            "request",
            "definition",
            "parent",
            "paths",
            "created_at",
            "deadline_at",
            "settings",
            "parent_state_version",
            "output_manifest",
        )
    }


def prepare(request_file: Path) -> dict[str, object]:
    """Freeze one call before public mutation; exact replay preserves its deadline."""
    req = _read_request(request_file)
    journal = Journal(Path(str(req["call_home"])), str(req["call_key"]))
    # Refuse unsafe parent/catalog locations before even creating lock files.
    view = _parent(req)
    if not journal.path.exists():
        _definition(req)
    with journal.lock():
        if journal.path.exists():
            saved = journal.load()
            if saved["request"] != req:
                raise SubworkflowError("call_conflict")
            return saved
        if os.path.lexists(journal.root):
            raise SubworkflowError("call_root_not_owned")
        output = Path(str(req["output_root"])) / str(req["output_ref"])
        if os.path.lexists(output):
            raise SubworkflowError("output_not_owned")
        settings = {
            key: view[key]
            for key in (
                "path",
                "env_allowlist",
                "host_paths",
                "runtime_defaults",
                "operator_policy",
            )
        }
        paths = {
            name: str(journal.root / name) for name in ("workflow", "project", "state")
        }
        created = timestamp()
        record: dict[str, object] = {
            "format": JOURNAL_FORMAT,
            "request": req,
            "request_sha256": digest(req),
            "definition": req["definition"],
            "parent": req["parent"],
            "paths": paths,
            "created_at": created,
            "deadline_at": (
                _date(created) + timedelta(seconds=cast(int, req["wait_seconds"]))
            ).isoformat(),
            "cleanup_deadline_at": None,
            "settings": settings,
            "parent_state_version": view["state_version"],
            "envelopes": {
                "create": envelope(
                    "create_instance",
                    req,
                    instance_id=None,
                    expected=0,
                    payload={
                        "workflow_file": paths["workflow"] + "/workflow.json",
                        "project_root": paths["project"],
                        **settings,
                    },
                ),
                "start": None,
                "cancel": None,
            },
            "receipts": {},
            "instance_id": None,
            "run_id": None,
            "observation": {},
            "phase": "prepared",
            "diagnostic": None,
            "output_manifest": str(output),
            "controllers": [],
            "cancel_intent": None,
            "cancellations": {},
            "copy_prepared": False,
            "saved_result": None,
        }
        record["authority_sha256"] = digest(_authority(record))
        journal.root.mkdir()
        journal.save(record)
        return record


def inspect(call_home: Path, call_key: str) -> dict[str, object]:
    """Read retained evidence only, without controller launch or public mutation."""
    return Journal(call_home, call_key).load()


def _update(journal: Journal, **changes: object) -> dict[str, object]:
    with journal.lock():
        saved = journal.load()
        saved.update(changes)
        journal.save(saved)
        return saved


def _copy(journal: Journal) -> None:
    saved = journal.load()
    req, paths = mapping(saved["request"]), mapping(saved["paths"])
    workflow, project = Path(str(paths["workflow"])), Path(str(paths["project"]))
    if not saved["copy_prepared"] and (
        os.path.lexists(workflow) or os.path.lexists(project)
    ):
        raise SubworkflowError("child_copy_not_owned")
    try:
        if not workflow.exists():
            _definition(req)
            result = catalog.init_template(
                str(mapping(req["definition"])["selector"]), workflow
            )
            if (
                mapping(result["provenance"])["sha256"]
                != mapping(req["definition"])["sha256"]
            ):
                raise SubworkflowError("definition_mismatch")
        if (
            catalog.workflow_fingerprint(workflow)
            != mapping(req["definition"])["sha256"]
        ):
            raise SubworkflowError("definition_mismatch")
        canonical_path(str(project))
        project.mkdir(exist_ok=True)
        graph = mapping(catalog.strict_json((workflow / "workflow.json").read_bytes()))
        for plane in mapping(graph.get("planes")).values():
            workspace = mapping(plane).get("workspace")
            if not isinstance(workspace, str) or not workspace:
                raise SubworkflowError("invalid_workspace")
            target = project / workspace
            canonical_path(str(target))
            if target != project and project not in target.parents:
                raise SubworkflowError("invalid_workspace")
            target.mkdir(parents=True, exist_ok=True)
        manifest = {
            "format": "millrace-plus-subworkflow-inputs/v1",
            "identity": {
                "call_home": req["call_home"],
                "call_key": req["call_key"],
                "output_root": req["output_root"],
            },
            "inputs": req["inputs"],
        }
        file = project / "subworkflow-inputs.json"
        if file.exists():
            if file.read_bytes() != canonical(manifest) + b"\n":
                raise SubworkflowError("input_manifest_conflict")
        else:
            atomic_json(file, manifest, limit=65536 + 8192)
        _update(journal, copy_prepared=True)
    except catalog.CatalogError as exc:
        raise SubworkflowError(exc.code) from exc


def _projection(view: dict[str, object]) -> dict[str, object]:
    """Select writer/status facts; omit domain output, captured inputs and revisions."""
    return {
        "id": view["id"],
        "status": view["status"],
        "state_version": view["state_version"],
        "quiescent": view["quiescent"],
        "pause_reason": view.get("pause_reason"),
        "inflight_invocations": view["inflight_invocations"],
        "invocations": [
            {k: row[k] for k in ("id", "status", "writer_state")}
            for row in cast(list[dict[str, object]], view["invocations"])
        ],
        "completions": [
            {k: row[k] for k in ("invocation_id", "effective_writer")}
            for row in cast(list[dict[str, object]], view["completions"])
        ],
    }


def closed(view: dict[str, object], *, attention: bool = False) -> bool:
    """Require exact public terminal/quiescent/stopped-writer facts."""
    return (
        view.get("status") in (TERMINAL | ({"paused"} if attention else set()))
        and view.get("quiescent") is True
        and view.get("inflight_invocations") == []
        and isinstance(view.get("invocations"), list)
        and isinstance(view.get("completions"), list)
        and all(
            mapping(row).get("writer_state") == "stopped"
            for row in cast(list[object], view["invocations"])
        )
        and all(
            mapping(row).get("effective_writer") == "stopped"
            for row in cast(list[object], view["completions"])
        )
    )


def _receipt(journal: Journal, operation: str, value: object) -> bool:
    row = mapping(value)
    saved = journal.load()
    sent = mapping(saved["envelopes"])[operation]
    secondary = None
    if operation == "cancel":
        secondary = mapping(saved["cancellations"]).get(str(row.get("request_id")))
        if secondary is not None:
            sent = mapping(secondary)["envelope"]
    if (
        not isinstance(sent, dict)
        or row.get("request_id") != sent["request_id"]
        or row.get("operation") != sent["operation"]
        or row.get("request_digest") != public_request_digest(sent)
    ):
        return False
    if secondary is not None and mapping(secondary)["receipt"] == row:
        return True
    if secondary is None and mapping(saved["receipts"]).get(operation) == row:
        return True
    with journal.lock():
        saved = journal.load()
        receipts = mapping(saved["receipts"])
        if secondary is not None:
            mapping(mapping(saved["cancellations"])[str(row["request_id"])])[
                "receipt"
            ] = row
        if (
            secondary is None
            or row["request_id"] == mapping(saved["cancel_intent"])["request_id"]
        ):
            receipts[operation] = row
        if row.get("status") == "applied":
            if operation == "create":
                saved["instance_id"] = row["instance_id"]
                saved["phase"] = "created"
            elif operation == "start":
                saved["run_id"] = row["run_id"]
                saved["phase"] = "observing"
        journal.save(saved)
    return True


def _refresh(journal: Journal) -> dict[str, object]:
    saved = journal.load()
    if saved["instance_id"] is None:
        return saved
    home = Path(str(mapping(saved["paths"])["state"]))
    instance = _control().inspect_instance(str(saved["instance_id"]), state_home=home)
    if mapping(saved["envelopes"])["start"] is not None:
        for receipt in cast(list[dict[str, object]], instance["controls"]):
            _receipt(journal, "start", receipt)
    saved = journal.load()
    if saved["run_id"] is not None:
        run = _control().inspect_run(
            str(saved["instance_id"]), str(saved["run_id"]), state_home=home
        )
        if run["id"] != saved["run_id"]:
            raise SubworkflowError("wrong_run_observation")
        try:
            projected = _projection(run)
            if saved["observation"] != projected:
                saved = _update(journal, observation=projected)
        except SubworkflowError as exc:
            if exc.code != "record_too_large":
                raise
            return _update(
                journal,
                phase="cleanup_required",
                diagnostic="observation_limit",
                observation={},
            )
        if mapping(saved["envelopes"])["cancel"] is not None:
            for receipt in cast(list[dict[str, object]], run["controls"]):
                _receipt(journal, "cancel", receipt)
    return journal.load()


def _environment(saved: dict[str, object]) -> dict[str, str]:
    settings = mapping(saved["settings"])
    env = {
        name: os.environ[name]
        for name in cast(list[str], settings["env_allowlist"])
        if name in os.environ
    }
    # HOME locates the same saved catalog/adapter configuration. No other ambient
    # variables pass into the controller unless the parent explicitly allowed them.
    if "HOME" in os.environ:
        env["HOME"] = os.environ["HOME"]
    env["PATH"] = os.pathsep.join(cast(list[str], settings["path"]))
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _launch(
    journal: Journal,
    operation: str,
    *,
    cancel_id: str | None = None,
    recover_create: bool = False,
) -> ControlProcess | None:
    # The final intent check and send share one short lock. Cancel cannot become
    # known between this check and Popen. No lock is held while a child runs.
    with journal.lock():
        saved = journal.load()
        if (
            operation in {"create", "start"}
            and saved["cancel_intent"] is not None
            and not (operation == "create" and recover_create)
        ):
            if operation == "start":
                saved["phase"] = "created"
                journal.save(saved)
            return None
        sent = mapping(mapping(saved["envelopes"])[operation])
        if cancel_id is not None:
            sent = mapping(
                mapping(mapping(saved["cancellations"])[cancel_id])["envelope"]
            )
        saved["phase"] = {
            "create": "creating",
            "start": "starting",
            "cancel": "cancel_requested",
        }[operation]
        process = ControlProcess(
            sent,
            Path(str(mapping(saved["paths"])["state"])),
            journal.root / "logs",
            operation,
            env=_environment(saved),
        )
        row = {"pid": None, "operation": operation, "joined": False}
        cast(list[object], saved["controllers"]).append(row)
        saved["diagnostic"] = "controller_launch_unresolved"
        try:
            journal.save(saved)
            process.start()
            row["pid"] = process.pid
            saved["diagnostic"] = None
            journal.save(saved)
        except BaseException:
            # The pre-send reservation survives even a persistent storage error.
            # Keep the actual PID when storage recovers, but never wait without
            # a bound or treat closing our pipes as descendant closure.
            if process.process is None:
                row["joined"] = True
            else:
                row["pid"] = process.pid
                try:
                    row["joined"] = process.finish(0.1)
                except OSError:
                    pass  # The retained reservation still requires inspection.
            saved.update(
                phase="cleanup_required", diagnostic="controller_launch_failed"
            )
            try:
                journal.save(saved)
            except OSError:
                pass  # The already durable unresolved reservation fails closed.
            finally:
                process.detach()
            raise
        return process


def _poll(journal: Journal, processes: list[ControlProcess]) -> bool:
    overflow = False
    for process in processes:
        done = process.poll()
        overflow |= process.overflow
        for line in bytes(process.output["stdout"]).splitlines():
            try:
                value = catalog.strict_json(line)
            except catalog.CatalogError:
                continue
            if isinstance(value, dict):
                _receipt(journal, process.operation, value)
        if done:
            with journal.lock():
                saved = journal.load()
                changed = False
                for row in cast(list[dict[str, object]], saved["controllers"]):
                    if row["pid"] == process.pid and not row["joined"]:
                        row["joined"] = True
                        changed = True
                if changed:
                    journal.save(saved)
    return overflow


def _controllers_closed(saved: dict[str, object]) -> bool:
    # A recovered controller is not ours to waitpid. An extant PID remains
    # uncertain (including possible PID reuse); an absent PID proves no survivor.
    return all(
        row["joined"] is True
        or (row["pid"] is not None and not alive(cast(int, row["pid"])))
        for row in cast(list[dict[str, object]], saved["controllers"])
    )


def _intent(journal: Journal, identifier: str) -> dict[str, object]:
    with journal.lock():
        saved = journal.load()
        cancellations = mapping(saved["cancellations"])
        if identifier not in cancellations:
            cancellations[identifier] = {
                "created_at": timestamp(),
                "envelope": None,
                "receipt": None,
            }
        if saved["cancel_intent"] is None:
            now = timestamp()
            saved["cancel_intent"] = {"request_id": identifier, "created_at": now}
            saved["cleanup_deadline_at"] = (
                _date(now)
                + timedelta(
                    seconds=cast(int, mapping(saved["request"])["cleanup_seconds"])
                )
            ).isoformat()
        if saved["run_id"] is not None:
            for key, item in cancellations.items():
                if mapping(item)["envelope"] is None:
                    mapping(item)["envelope"] = envelope(
                        "cancel_run",
                        mapping(saved["request"]),
                        instance_id=str(saved["instance_id"]),
                        run_id=str(saved["run_id"]),
                        explicit_id=key,
                    )
            first_id = str(mapping(saved["cancel_intent"])["request_id"])
            mapping(saved["envelopes"])["cancel"] = mapping(cancellations[first_id])[
                "envelope"
            ]
        journal.save(saved)
        return saved


def cancel(call_home: Path, call_key: str, request_id: str) -> dict[str, object]:
    """Persist a caller UUID and send its exact public cancellation."""
    # Validate the caller UUID even when the child has not been dispatched.
    envelope("cancel_run", {}, instance_id=None, explicit_id=request_id)
    journal = Journal(call_home, call_key)
    saved = _intent(journal, request_id)
    if saved["run_id"] is None:
        return saved
    process = _launch(journal, "cancel", cancel_id=request_id)
    if process is not None:
        try:
            while not process.poll() and seconds_left(saved["cleanup_deadline_at"]) > 0:
                _poll(journal, [process])
                time.sleep(0.03)
            _poll(journal, [process])
            if not process.joined:
                _update(
                    journal,
                    phase="cleanup_required",
                    diagnostic="cancel_sender_not_joined",
                )
        finally:
            if not process.joined:
                process.detach()
    return _refresh(journal)


def reconcile(call_home: Path, call_key: str) -> dict[str, object]:
    """Refresh exact public observations only; never resume/recover/assert stopped."""
    journal = Journal(call_home, call_key)
    saved = _refresh(journal)
    view = mapping(saved["observation"])
    if view and closed(view) and _controllers_closed(saved):
        return _update(journal, phase="terminal", diagnostic=None)
    if saved["instance_id"] is not None and saved["phase"] not in {
        "attention_required",
        "terminal",
    }:
        return _update(
            journal, phase="cleanup_required", diagnostic="closure_unresolved"
        )
    return saved


def _result(
    journal: Journal, outcome: str, diagnostic: str | None = None
) -> dict[str, object]:
    saved = journal.load()
    req = mapping(saved["request"])
    output = canonical_path(saved["output_manifest"])
    if output.exists():
        try:
            prior = mapping(catalog.strict_json(output.read_bytes()))
        except catalog.CatalogError as exc:
            raise SubworkflowError("output_not_owned") from exc
        if prior.get("call_identity") != saved["request_sha256"]:
            raise SubworkflowError("output_not_owned")
    view = mapping(saved["observation"])
    manifest = {
        "format": "millrace-plus-subworkflow-result/v1",
        "call_identity": saved["request_sha256"],
        "call_key": req["call_key"],
        "definition": saved["definition"],
        "instance_id": saved["instance_id"],
        "run_id": saved["run_id"],
        "dispatch": "started" if saved["run_id"] else "not_started",
        "status": view.get("status"),
        "observation": view,
        "receipts": saved["receipts"],
        "outcome": outcome,
        "diagnostic": diagnostic,
        "controllers": saved["controllers"],
    }
    try:
        atomic_json(output, manifest, limit=262144)
    except SubworkflowError as exc:
        if exc.code != "record_too_large":
            raise
        outcome, diagnostic = "CHILD_UNCERTAIN", "result_limit"
        manifest.update(
            outcome=outcome, diagnostic=diagnostic, observation={}, controllers=[]
        )
        atomic_json(output, manifest, limit=262144)
    result = {
        "outcome": outcome,
        "summary": diagnostic or outcome,
        "refs": [req["output_ref"]],
    }
    phase = (
        "terminal"
        if outcome
        in {"CHILD_SUCCEEDED", "CHILD_FAILED", "CHILD_CANCELLED", "CHILD_REFUSED"}
        else "attention_required"
        if outcome == "CHILD_ATTENTION"
        else "cleanup_required"
    )
    _update(journal, saved_result=result, phase=phase, diagnostic=diagnostic)
    return result


def call(request_file: Path) -> dict[str, object]:
    """Observe one independent child until attention or finite cleanup settles."""
    saved = prepare(request_file)
    req = mapping(saved["request"])
    journal = Journal(Path(str(req["call_home"])), str(req["call_key"]))
    processes: list[ControlProcess] = []
    interrupted = threading.Event()
    handlers = {}
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGTERM, signal.SIGINT):
            handlers[sig] = signal.signal(sig, lambda *_: interrupted.set())
    try:
        with journal.lock(observer=True):
            saved = journal.load()
            if saved["phase"] == "terminal" and saved["saved_result"] is not None:
                previous = mapping(saved["saved_result"])
                expected = OUTCOMES.get(
                    str(mapping(saved["observation"]).get("status"))
                )
                if expected is None or previous["outcome"] == expected:
                    output = canonical_path(saved["output_manifest"])
                    if not output.is_file():
                        raise SubworkflowError("output_missing")
                    manifest = mapping(catalog.strict_json(output.read_bytes()))
                    if (
                        manifest.get("call_identity") != saved["request_sha256"]
                        or manifest.get("outcome") != previous["outcome"]
                    ):
                        raise SubworkflowError("output_not_owned")
                    return previous
            while True:
                overflow = _poll(journal, processes)
                _refresh(journal)
                # A sender holds this lock from reservation through PID save.
                # Do not treat its transient null PID as a crashed launch.
                with journal.lock():
                    saved = journal.load()
                    unresolved_controller = any(
                        row["pid"] is None and not row["joined"]
                        for row in cast(list[dict[str, object]], saved["controllers"])
                    )
                if saved["diagnostic"] == "observation_limit":
                    return _result(journal, "CHILD_UNCERTAIN", "observation_limit")
                if overflow:
                    return _result(journal, "CHILD_UNCERTAIN", "control_output_limit")
                if interrupted.is_set() or seconds_left(saved["deadline_at"]) <= 0:
                    _intent(
                        journal,
                        request_id(
                            req,
                            "deadline_cancel"
                            if not interrupted.is_set()
                            else "interrupt_cancel",
                        ),
                    )
                    saved = journal.load()
                cancel_intent = saved["cancel_intent"]
                view = mapping(saved["observation"])
                controllers_settled = _controllers_closed(saved) and all(
                    p.joined for p in processes
                )
                if unresolved_controller:
                    return _result(
                        journal, "CHILD_UNCERTAIN", "controller_identity_unresolved"
                    )
                if view and controllers_settled:
                    if closed(view):
                        return _result(journal, OUTCOMES[str(view["status"])])
                    if view.get("status") == "paused":
                        if closed(view, attention=True) and cancel_intent is None:
                            return _result(journal, "CHILD_ATTENTION")
                        if not closed(view, attention=True):
                            return _result(
                                journal, "CHILD_UNCERTAIN", "writer_unresolved"
                            )
                if cancel_intent is not None:
                    identifier = str(mapping(cancel_intent)["request_id"])
                    if saved["run_id"] is not None:
                        saved = _intent(journal, identifier)
                        if not any(p.operation == "cancel" for p in processes):
                            process = _launch(journal, "cancel")
                            if process:
                                processes.append(process)
                    elif saved["phase"] in {"prepared", "created"} or (
                        saved["instance_id"] is not None
                        and mapping(saved["envelopes"])["start"] is None
                    ):
                        if controllers_settled:
                            return _result(
                                journal, "CHILD_CANCELLED", "dispatch_prevented"
                            )
                    elif (
                        mapping(mapping(saved["receipts"]).get("create", {})).get(
                            "status"
                        )
                        == "refused"
                        and mapping(saved["envelopes"])["start"] is None
                        and controllers_settled
                    ):
                        return _result(journal, "CHILD_CANCELLED", "dispatch_prevented")
                    if seconds_left(saved["cleanup_deadline_at"]) <= 0:
                        return _result(journal, "CHILD_UNCERTAIN", "cleanup_deadline")
                # Create can be replayed exactly to recover its receipt. An
                # unresolved start never replays: it might create new execution.
                if saved["instance_id"] is None and cancel_intent is None:
                    receipts = mapping(saved["receipts"])
                    if (
                        "create" in receipts
                        and mapping(receipts["create"]).get("status") == "refused"
                    ):
                        if controllers_settled:
                            return _result(journal, "CHILD_REFUSED", "create_refused")
                    if controllers_settled and not any(
                        p.operation == "create" for p in processes
                    ):
                        _copy(journal)
                        process = _launch(journal, "create")
                        if process:
                            processes.append(process)
                elif (
                    saved["instance_id"] is None
                    and cancel_intent is not None
                    and saved["phase"] == "creating"
                ):
                    # A known pre-start cancel can finish an unknown create only.
                    if not any(
                        p.operation == "create" for p in processes
                    ) and _controllers_closed(saved):
                        process = _launch(journal, "create", recover_create=True)
                        if process:
                            processes.append(process)
                elif (
                    saved["instance_id"] is not None
                    and saved["run_id"] is None
                    and cancel_intent is None
                    and controllers_settled
                ):
                    if mapping(saved["envelopes"])["start"] is None:
                        validation = _control().validate_revision(
                            str(saved["instance_id"]),
                            state_home=Path(str(mapping(saved["paths"])["state"])),
                        )
                        if validation.get("valid") is False:
                            return _result(
                                journal, "CHILD_REFUSED", "child_validation_refused"
                            )
                        with journal.lock():
                            record = journal.load()
                            mapping(record["envelopes"])["start"] = envelope(
                                "start_run",
                                req,
                                instance_id=str(record["instance_id"]),
                                expected=1,
                                payload={"edit_mode": "locked"},
                            )
                            journal.save(record)
                        process = _launch(journal, "start")
                        if process:
                            processes.append(process)
                    elif not any(
                        p.operation == "start" and not p.joined for p in processes
                    ):
                        receipts = mapping(saved["receipts"])
                        if (
                            "start" in receipts
                            and mapping(receipts["start"]).get("status") == "refused"
                        ):
                            return _result(journal, "CHILD_REFUSED", "start_refused")
                        return _result(
                            journal, "CHILD_UNCERTAIN", "start_identity_unresolved"
                        )
                if not processes:
                    time.sleep(0.05)
                else:
                    for process in processes:
                        if not process.joined:
                            process.poll(0.03)
                    time.sleep(0.01)
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
        for process in processes:
            if not process.joined:
                # Never turn helper exit/pipe closure into descendant closure.
                process.detach()
