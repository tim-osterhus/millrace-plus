"""One finite OS-backed child advance, with durable envelopes and no service loop."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from . import catalog
from .subworkflow_records import (
    SubworkflowError,
    _id,
    atomic_json,
    canonical,
    canonical_path,
    digest,
    envelope,
    mapping,
    overlaps,
    public_request_digest,
    timestamp,
)
from .subworkflow_records import (
    request_id as operation_id,
)
from .subworkflow_v2_records import (
    JOURNAL_FORMAT,
    RESULT_LIMIT,
    Journal,
    authority,
    bounded,
    parse_request,
    parse_result,
    preflight,
    result_manifest,
    validate_projection,
    validate_result,
    write_inputs,
)
from .workflow_os_client import (
    WorkflowOSClient,
    load_profile,
    request_body,
    validate_call,
    validate_capabilities,
    validate_receipt,
)

__all__ = ["advance", "inspect", "cancel", "reconcile", "closed", "has_join_proof"]
SETTINGS = (
    "path",
    "env_allowlist",
    "host_paths",
    "runtime_defaults",
    "operator_policy",
)
TERMINAL = {"succeeded", "failed", "cancelled"}


class Client(Protocol):
    """Only public OS calls; fake clients implement the same finite deadline seam."""

    def capabilities(self, *, deadline: float) -> dict[str, object]: ...
    def submit(
        self,
        envelope: dict[str, object],
        *,
        deadline: float,
    ) -> dict[str, object]: ...
    def observe(
        self,
        envelope: dict[str, object],
        *,
        deadline: float,
    ) -> dict[str, object]: ...
    def read(
        self,
        instance: str,
        run: str | None = None,
        *,
        deadline: float,
    ) -> dict[str, object]: ...


def _definition(request: dict[str, object]) -> Path:
    # Reuse v1 catalog pinning, never its local controller or state-home paths.
    from .subworkflows import _definition as selected_definition

    return selected_definition(request)


def _parent(
    request: dict[str, object],
    client: Client,
    deadline: float,
) -> dict[str, object]:
    parent = mapping(request["parent"])
    view = client.read(str(parent["instance_id"]), deadline=deadline)
    if view.get("instance_id") != parent["instance_id"]:
        raise SubworkflowError("wrong_parent_identity")
    root = canonical_path(view.get("project_root"))
    output = canonical_path(request["output_root"])
    if output != root and root not in output.parents:
        raise SubworkflowError("output_outside_parent")
    home = canonical_path(request["call_home"])
    sources = (root, canonical_path(str(Path(str(view["workflow_file"])).parent)))
    if any(overlaps(home, source) for source in sources):
        raise SubworkflowError("parent_child_overlap")
    if "run_id" in parent:
        run = client.read(
            str(parent["instance_id"]),
            str(parent["run_id"]),
            deadline=deadline,
        )
        if (
            run.get("id") != parent["run_id"]
            or run.get("instance_id") != parent["instance_id"]
        ):
            raise SubworkflowError("wrong_parent_identity")
        if "plane_id" in parent and parent["plane_id"] not in mapping(
            run.get("planes")
        ):
            raise SubworkflowError("parent_plane_missing")
    try:
        catalog.check_ownership(home / "workflow.json", home)
    except catalog.CatalogError as exc:
        raise SubworkflowError(exc.code) from exc
    return view


def _prepare(
    journal: Journal,
    request: dict[str, object],
    raw: bytes,
    *,
    os_home: str,
    claim_sha256: str | None,
    client: Client,
    deadline: float,
) -> dict[str, object]:
    profile = load_profile(request["os_profile"], home=os_home)
    validate_capabilities(client.capabilities(deadline=deadline), os_home)
    if journal.path.exists():
        saved = journal.load()
        if (
            saved["request"] != request
            or saved["request_raw"] != raw.decode("utf-8")
            or saved["claim_sha256"] != claim_sha256
            or saved["os_home"] != os_home
            or saved["os_origin"] != profile["os_origin"]
        ):
            raise SubworkflowError("call_conflict")
        return saved
    parent = _parent(request, client, deadline)
    source = _definition(request)
    if overlaps(journal.home, source):
        raise SubworkflowError("catalog_child_overlap")
    if os.path.lexists(journal.root):
        raise SubworkflowError("call_root_not_owned")
    output = Path(str(request["output_root"])) / str(request["output_ref"])
    if os.path.lexists(output):
        raise SubworkflowError("output_not_owned")
    settings = {key: parent[key] for key in SETTINGS}
    paths = {name: str(journal.root / name) for name in ("workflow", "project")}
    record: dict[str, object] = {
        "format": JOURNAL_FORMAT,
        "request": request,
        "request_raw": raw.decode("utf-8"),
        "request_sha256": digest(request),
        "claim_sha256": claim_sha256,
        "definition": request["definition"],
        "parent": request["parent"],
        "paths": paths,
        "created_at": timestamp(),
        "settings": settings,
        "parent_state_version": parent["state_version"],
        "os_home": os_home,
        "os_origin": profile["os_origin"],
        "os_profile": request["os_profile"],
        "envelopes": {
            "create": envelope(
                "create_instance",
                request,
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
        "reservations": {"create": "unsent", "start": "unsent", "cancel": "unsent"},
        "receipts": {},
        "instance_id": None,
        "run_id": None,
        "os_observations": [],
        "observation": None if journal.local_source is not None else {},
        "phase": "prepared",
        "diagnostic": None,
        "copy_prepared": False,
        "output_manifest": str(output),
        "saved_result": None,
        "cancel_intent": None,
        "cancellations": {},
    }
    if journal.local_source is not None:
        from .subworkflow_persistent_records import JOURNAL_FORMAT as persistent_marker

        record.update(format=persistent_marker, local_source=journal.local_source)
    record["authority_sha256"] = digest(authority(record))
    record["record_sha256"] = digest(record)
    preflight(record)
    # Validate before even creating the call root. Locks use only the call home.
    from .subworkflow_v2_records import validate_journal

    validate_journal(
        record, journal.home, journal.key, _local_source=journal.local_source
    )
    with journal.lock():
        if journal.path.exists() or os.path.lexists(journal.root):
            raise SubworkflowError("call_conflict")
        journal.root.mkdir()
        journal.save(record)
    return record


def _save(journal: Journal, record: dict[str, object]) -> None:
    with journal.lock():
        journal.save(record)


def _copy(journal: Journal, record: dict[str, object]) -> None:
    if record["copy_prepared"]:
        return
    request = mapping(record["request"])
    paths = mapping(record["paths"])
    workflow, project = Path(str(paths["workflow"])), Path(str(paths["project"]))
    if record["phase"] == "prepared":
        if os.path.lexists(workflow) or os.path.lexists(project):
            raise SubworkflowError("child_copy_not_owned")
        record["phase"] = "copying"
        _save(journal, record)
    try:
        _definition(request)
        if not workflow.exists():
            catalog.init_template(
                str(mapping(request["definition"])["selector"]),
                workflow,
            )
        if (
            catalog.workflow_fingerprint(workflow)
            != mapping(request["definition"])["sha256"]
        ):
            raise SubworkflowError("definition_mismatch")
        canonical_path(str(project))
        project.mkdir(exist_ok=True)
        graph = mapping(catalog.strict_json((workflow / "workflow.json").read_bytes()))
        for plane in mapping(graph.get("planes")).values():
            workspace = mapping(plane).get("workspace")
            if not isinstance(workspace, str) or not workspace:
                raise SubworkflowError("invalid_workspace")
            target = canonical_path(str(project / workspace))
            if target != project and project not in target.parents:
                raise SubworkflowError("invalid_workspace")
            target.mkdir(parents=True, exist_ok=True)
        write_inputs(project / "subworkflow-inputs.json", request)
    except catalog.CatalogError as exc:
        raise SubworkflowError(exc.code) from exc
    record["copy_prepared"] = True
    record["phase"] = "prepared"
    _save(journal, record)


def _envelopes(record: dict[str, object]) -> dict[str, object]:
    envs = mapping(record["envelopes"])
    return {"create": envs["create"], "start": envs["start"]} | {
        "cancel:" + identifier: mapping(row)["envelope"]
        for identifier, row in mapping(record["cancellations"]).items()
    }


def _reservation(record: dict[str, object], operation: str) -> object:
    if operation.startswith("cancel:"):
        return mapping(mapping(record["cancellations"])[operation[7:]])["reservation"]
    return mapping(record["reservations"])[operation]


def _set_reservation(record: dict[str, object], operation: str, state: str) -> None:
    if operation.startswith("cancel:"):
        mapping(mapping(record["cancellations"])[operation[7:]])["reservation"] = state
        _cancel_aliases(record)
    else:
        mapping(record["reservations"])[operation] = state


def _cancel_aliases(record: dict[str, object]) -> None:
    intent = record["cancel_intent"]
    if intent is None:
        return
    first = mapping(
        mapping(record["cancellations"])[str(mapping(intent)["request_id"])]
    )
    mapping(record["envelopes"])["cancel"] = first["envelope"]
    mapping(record["reservations"])["cancel"] = first["reservation"]
    if first["receipt"] is not None:
        mapping(record["receipts"])["cancel"] = first["receipt"]


def _cancel_rows(journal: Journal, record: dict[str, object]) -> None:
    if record["run_id"] is None:
        return
    before = canonical(record)
    for identifier, value in mapping(record["cancellations"]).items():
        row = mapping(value)
        if row["envelope"] is None:
            row["envelope"] = envelope(
                "cancel_run",
                mapping(record["request"]),
                instance_id=str(record["instance_id"]),
                run_id=str(record["run_id"]),
                explicit_id=identifier,
            )
    _cancel_aliases(record)
    if canonical(record) != before:
        _save(journal, record)


def _receipt(record: dict[str, object], operation: str, value: object) -> None:
    sent = mapping(_envelopes(record)[operation])
    row = validate_receipt(value, sent)
    cancellation = (
        mapping(mapping(record["cancellations"])[operation[7:]])
        if operation.startswith("cancel:")
        else None
    )
    previous = (
        cancellation["receipt"]
        if cancellation is not None
        else mapping(record["receipts"]).get(operation)
    )
    if previous is not None:
        previous_row = mapping(previous)
        if previous_row == row:
            return
        # Public receipts can advance queued -> final, never rewrite a final effect.
        if previous_row.get("status") != "queued" and row.get("status") == "queued":
            return
        if previous_row.get("status") != "queued" and previous_row != row:
            raise SubworkflowError("receipt_conflict")
    if row.get("status") == "applied":
        if operation == "create":
            record["instance_id"] = _id(row.get("instance_id"))
            record["phase"] = "created"
        elif operation == "start":
            record["run_id"] = _id(row.get("run_id"))
            record["phase"] = "observing"
    if cancellation is not None:
        cancellation["receipt"] = row
        _cancel_aliases(record)
    else:
        mapping(record["receipts"])[operation] = row


def _observe(record: dict[str, object], operation: str, value: object) -> None:
    sent = mapping(_envelopes(record)[operation])
    row = validate_call(value, str(record["os_home"]), sent)
    history = record["os_observations"]
    if not isinstance(history, list):
        raise SubworkflowError("invalid_os_history")
    prior = next(
        (
            mapping(item)
            for item in reversed(history)
            if mapping(item)["request_id"] == sent["request_id"]
        ),
        None,
    )

    # A timestamp alone is not a new semantic fact or a journal write.
    def facts(item: dict[str, object]) -> dict[str, object]:
        return {k: v for k, v in item.items() if k != "observed_at"}

    if prior is None or facts(prior) != facts(row):
        history.append(row)
    _set_reservation(record, operation, "observed")
    if row["core_receipt"] is not None:
        _receipt(record, operation, row["core_receipt"])


def has_join_proof(record: dict[str, object], operation: str) -> bool:
    """Only an actual retained true for this exact envelope proves OS holder join."""
    value = (
        mapping(record["envelopes"])["cancel"]
        if operation == "cancel"
        else _envelopes(record)[operation]
    )
    if value is None:
        return False
    sent = mapping(value)
    for value in record["os_observations"]:
        row = mapping(value)
        if (
            row.get("home") != record["os_home"]
            or row.get("request_id") != sent["request_id"]
            or row.get("request_digest") != public_request_digest(sent)
            or row.get("source") != "os_owned_call"
            or row.get("holder_joined") is not True
        ):
            continue
        if all(
            sent[name] is None or row.get(name) == sent[name]
            for name in ("instance_id", "run_id")
        ):
            return True
    return False


def _refresh(
    journal: Journal,
    record: dict[str, object],
    client: Client,
    deadline: float,
    clock: Callable[[], float],
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    before = canonical(record)
    for operation, sent in _envelopes(record).items():
        if clock() >= deadline:
            break
        if sent is None or _reservation(record, operation) == "unsent":
            continue
        if operation == "create":
            receipt = mapping(record["receipts"]).get("create")
            # Journal validation checks retained call-v2 rows. An exact applied
            # receipt and its actual true join survive the ephemeral OS cache.
            if (
                receipt is not None
                and record["instance_id"] is not None
                and validate_receipt(receipt, mapping(sent))["status"] == "applied"
                and has_join_proof(record, operation)
            ):
                continue
        _observe(record, operation, client.observe(mapping(sent), deadline=deadline))
        if canonical(record) != before:
            _save(journal, record)
            before = canonical(record)
    instance = record["instance_id"]
    read_progress = False
    while instance is not None and clock() < deadline:
        view = client.read(str(instance), deadline=deadline)
        paths = mapping(record["paths"])
        if (
            view.get("instance_id") != instance
            or view.get("project_root") != paths["project"]
            or view.get("workflow_file") != paths["workflow"] + "/workflow.json"
            or any(
                view.get(key) != mapping(record["settings"])[key] for key in SETTINGS
            )
        ):
            raise SubworkflowError("wrong_child_identity")
        start = mapping(record["envelopes"])["start"]
        if start is not None:
            controls = view.get("controls")
            if not isinstance(controls, list):
                raise SubworkflowError("invalid_instance_observation")
            matches = [
                mapping(receipt)
                for receipt in controls
                if mapping(receipt).get("request_id") == mapping(start)["request_id"]
            ]
            if len(matches) > 1:
                raise SubworkflowError("ambiguous_start_receipt")
            if len(matches) == 1:
                _receipt(record, "start", matches[0])
        if record["run_id"] is not None and clock() < deadline:
            run = client.read(str(instance), str(record["run_id"]), deadline=deadline)
            validate_projection(run, instance, record["run_id"])
            if run.get("start_request_id") != operation_id(
                mapping(record["request"]),
                "start_run",
            ):
                raise SubworkflowError("wrong_start_identity")
            if journal.local_source is not None:
                from .subworkflow_persistent_records import (
                    _ReadProgress,
                    reduce_full_views,
                )

                try:
                    record["observation"] = reduce_full_views(
                        view,
                        run,
                        instance_id=str(instance),
                        run_id=str(record["run_id"]),
                        start_request_id=operation_id(
                            mapping(record["request"]), "start_run"
                        ),
                        paths=paths,
                        settings=mapping(record["settings"]),
                    )
                except _ReadProgress:
                    read_progress = True
                    remaining = deadline - clock()
                    if remaining > 0:
                        sleep(min(5, remaining))
                    if clock() >= deadline:
                        raise _ReadProgress("observation_window_expired") from None
                    continue
                read_progress = False
            else:
                record["observation"] = run
        break
    if read_progress:
        raise _ReadProgress("observation_window_expired")
    if canonical(record) != before:
        _save(journal, record)


def closed(view: dict[str, object], *, attention: bool = False) -> bool:
    """Core closure facts only; this makes no LAD CLOSE/ACK or queue-free claim."""
    return (
        view.get("status") in (TERMINAL | ({"paused"} if attention else set()))
        and view.get("quiescent") is True
        and view.get("inflight_invocations") == []
        and isinstance(view.get("invocations"), list)
        and isinstance(view.get("completions"), list)
        and all(
            mapping(row).get("writer_state") == "stopped" for row in view["invocations"]
        )
        and all(
            mapping(row).get("effective_writer") == "stopped"
            for row in view["completions"]
        )
        and isinstance(view.get("controls"), list)
        and all(
            mapping(row).get("status") in {"applied", "refused"}
            for row in view["controls"]
        )
    )


def _outcome(record: dict[str, object]) -> tuple[str, str | None]:
    envs = _envelopes(record)
    receipts = mapping(record["receipts"])
    sent = [
        name
        for name in envs
        if envs[name] is not None and _reservation(record, name) != "unsent"
    ]
    for operation in sent:
        latest = next(
            (
                mapping(item)
                for item in reversed(record["os_observations"])
                if mapping(item)["request_id"] == mapping(envs[operation])["request_id"]
            ),
            None,
        )
        if (
            latest is not None
            and latest["phase"] == "call_failed"
            and latest["core_receipt"] is None
        ):
            return "CHILD_UNCERTAIN", "os_call_failed"
        if has_join_proof(record, operation):
            continue
        if latest is not None and latest["holder_joined"] is None:
            return "CHILD_OBSERVATION_LOST", "holder_observation_lost"
    if record["cancel_intent"] is not None and record["run_id"] is None:
        create_done = _reservation(record, "create") == "unsent" or mapping(
            receipts.get("create", {})
        ).get("status") in {"applied", "refused"}
        start_prevented = (
            _reservation(record, "start") == "unsent"
            or mapping(receipts.get("start", {})).get("status") == "refused"
        )
        if (
            create_done
            and start_prevented
            and all(has_join_proof(record, name) for name in sent)
        ):
            return "CHILD_CANCELLED", "dispatch_prevented"
        if not create_done and all(has_join_proof(record, name) for name in sent):
            return "CHILD_UNCERTAIN", "create_identity_unresolved"
        if not start_prevented and all(has_join_proof(record, name) for name in sent):
            return "CHILD_UNCERTAIN", "start_identity_unresolved"
    for operation, value in receipts.items():
        row = mapping(value)
        if row["status"] == "refused":
            if operation == "create" and record["instance_id"] is None:
                return "CHILD_REFUSED", "create_refused"
            if operation == "start" and record["run_id"] is None:
                return "CHILD_REFUSED", "start_refused"
    run = mapping(record["observation"]) if record["observation"] is not None else {}
    closure = closed
    if "local_source" in record:
        from .subworkflow_persistent_records import closed as closure
    if run.get("status") in TERMINAL | {"paused"}:
        if not closure(run, attention=True):
            return "CHILD_UNCERTAIN", "writers_unresolved"
        if any(
            mapping(value).get("status") == "queued"
            for value in [
                *receipts.values(),
                *(
                    mapping(row)["receipt"]
                    for row in mapping(record["cancellations"]).values()
                    if mapping(row)["receipt"] is not None
                ),
            ]
        ):
            return "CHILD_UNCERTAIN", "controls_unresolved"
        if any(not has_join_proof(record, name) for name in sent):
            return "CHILD_UNCERTAIN", "holder_unjoined"
        return {
            "succeeded": "CHILD_SUCCEEDED",
            "failed": "CHILD_FAILED",
            "cancelled": "CHILD_CANCELLED",
            "paused": "CHILD_ATTENTION",
        }[str(run["status"])], None
    return "CHILD_RUNNING", None


def _join_tail_eligible(record: dict[str, object]) -> bool:
    if _outcome(record) != ("CHILD_UNCERTAIN", "holder_unjoined"):
        return False
    for operation, value in _envelopes(record).items():
        if value is None or _reservation(record, operation) == "unsent":
            continue
        if has_join_proof(record, operation):
            continue
        sent = mapping(value)
        latest = next(
            (
                mapping(row)
                for row in reversed(record["os_observations"])
                if mapping(row)["request_id"] == sent["request_id"]
            ),
            None,
        )
        if (
            latest is None
            or latest["source"] != "os_owned_call"
            or latest["holder_joined"] is not False
            or latest["phase"] == "call_failed"
        ):
            return False
        validate_call(latest, str(record["os_home"]), sent)
    return True


def _join_tail(
    journal: Journal,
    record: dict[str, object],
    client: Client,
    deadline: float,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> tuple[str, str | None]:
    # This is a read-only terminal/paused join wait, never the dispatch loop.
    first_poll = True
    while _join_tail_eligible(record):
        remaining = deadline - clock()
        if remaining <= 0:
            break
        if not first_poll:
            sleep(min(5, remaining))
        if clock() >= deadline:
            break
        _refresh(journal, record, client, deadline, clock, sleep)
        first_poll = False
    return _outcome(record)


def _observation_error_outcome(
    record: dict[str, object],
    error: SubworkflowError,
) -> tuple[str, str | None]:
    if "local_source" in record:
        from .subworkflow_persistent_records import _ReadProgress

        if isinstance(error, _ReadProgress):
            outcome, diagnostic = _outcome(record)
            if outcome in {
                "CHILD_UNCERTAIN",
                "CHILD_OBSERVATION_LOST",
                "CHILD_REFUSED",
            }:
                return outcome, diagnostic
            # No coherent current pair completed this window. Preserve prior
            # evidence and the occupied task; never promote it to completion.
            return "CHILD_RUNNING", "observation_window_expired"
    if error.code == "observation_window_expired":
        return _outcome(record)
    return "CHILD_UNCERTAIN", error.code


def _dispatch(
    journal: Journal,
    record: dict[str, object],
    operation: str,
    client: Client,
    deadline: float,
) -> None:
    sent = mapping(_envelopes(record)[operation])
    request_body(str(record["os_home"]), sent)
    validate_capabilities(
        client.capabilities(deadline=deadline),
        str(record["os_home"]),
    )
    # Unknown covers both crash-before-send and send-before-save gaps.
    # Only the exact idempotent create may be replayed; start is never resent.
    _set_reservation(record, operation, "unknown")
    record["phase"] = (
        "creating"
        if operation == "create"
        else "starting"
        if operation == "start"
        else "observing"
    )
    _save(journal, record)
    response = (
        client._submit_prevalidated(sent, deadline=deadline)
        if type(client) is WorkflowOSClient
        else client.submit(sent, deadline=deadline)
    )
    _observe(record, operation, response)
    _save(journal, record)


def _result(
    journal: Journal,
    record: dict[str, object],
    outcome: str,
    diagnostic: str | None,
) -> dict[str, object]:
    manifest = result_manifest(record, outcome, diagnostic)
    try:
        validate_result(manifest, _local_source=journal.local_source)
    except SubworkflowError as exc:
        if exc.code != "record_too_large":
            raise
        outcome, diagnostic = "CHILD_UNCERTAIN", "result_limit"
        manifest = result_manifest(record, outcome, diagnostic)
        if journal.local_source is not None:
            # A diagnostic may omit the summary, never sent-control or join facts.
            manifest.update(observation=None, status=None)
        else:
            manifest.update(observation={}, receipts={}, os_observations=[])
        validate_result(manifest, _local_source=journal.local_source)
    result = {
        "outcome": outcome,
        "summary": diagnostic or outcome,
        "refs": [mapping(record["request"])["output_ref"]],
    }
    before = canonical(record)
    record["saved_result"], record["diagnostic"] = result, diagnostic
    record["phase"] = (
        "observing"
        if outcome == "CHILD_RUNNING"
        else "terminal"
        if outcome in {"CHILD_SUCCEEDED", "CHILD_FAILED", "CHILD_CANCELLED"}
        else "held"
    )
    preflight(record)
    output = canonical_path(record["output_manifest"])
    raw = bounded(manifest, RESULT_LIMIT)
    previous = None
    if output.exists():
        try:
            with output.open("rb") as stream:
                previous = stream.read(RESULT_LIMIT + 1)
            if journal.local_source is not None:
                from .workflow_os_client import _utf8_json

                owner = validate_result(
                    _utf8_json(previous, RESULT_LIMIT),
                    _local_source=journal.local_source,
                )
            else:
                owner = parse_result(previous)
        except (OSError, SubworkflowError):
            raise SubworkflowError("output_not_owned") from None
        if owner["call_identity"] != record["request_sha256"]:
            raise SubworkflowError("output_not_owned")
    # Crash between manifest and journal is repaired with the same canonical result.
    if previous != raw:
        atomic_json(output, manifest, limit=RESULT_LIMIT)
    if canonical(record) != before:
        _save(journal, record)
    return result


def inspect(call_home: Path, call_key: str) -> dict[str, object]:
    return Journal(call_home, call_key).load()


def _retained_client(
    record: dict[str, object], client: Client | None, clock: Callable[[], float]
) -> Client:
    profile = load_profile(record["os_profile"], home=str(record["os_home"]))
    if profile["os_origin"] != record["os_origin"]:
        raise SubworkflowError("call_conflict")
    return client or WorkflowOSClient(
        record["os_profile"], str(record["os_home"]), clock=clock
    )


def _pending_cancels(
    journal: Journal,
    record: dict[str, object],
    client: Client,
    deadline: float,
    clock: Callable[[], float],
) -> None:
    _cancel_rows(journal, record)
    for name, sent in _envelopes(record).items():
        if clock() >= deadline:
            break
        if (
            name.startswith("cancel:")
            and sent is not None
            and _reservation(record, name) == "unsent"
        ):
            _dispatch(journal, record, name, client, deadline)


def cancel(
    call_home: Path,
    call_key: str,
    request_id: str,
    *,
    client: Client | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    _local_source: dict[str, object] | None = None,
) -> dict[str, object]:
    """Persist explicit intent before sending; never restart an ambiguous child."""
    identifier = _id(request_id)
    journal = Journal(call_home, call_key, _local_source=_local_source)
    with journal.lock(observer=True):
        record = journal.load()
        connection = _retained_client(record, client, clock)
        ids = {
            mapping(value)["request_id"]
            for name, value in _envelopes(record).items()
            if name in {"create", "start"} and value is not None
        }
        if identifier in ids or identifier == operation_id(
            mapping(record["request"]), "start_run"
        ):
            raise SubworkflowError("cancel_id_conflict")
        rows = mapping(record["cancellations"])
        if identifier not in rows:
            created = timestamp()
            rows[identifier] = {
                "created_at": created,
                "envelope": None,
                "reservation": "unsent",
                "receipt": None,
            }
            if record["cancel_intent"] is None:
                record["cancel_intent"] = {
                    "request_id": identifier,
                    "created_at": created,
                }
            _save(journal, record)
        deadline = clock() + int(mapping(record["request"])["cleanup_seconds"])
        try:
            while clock() < deadline:
                _refresh(journal, record, connection, deadline, clock, sleep)
                _pending_cancels(journal, record, connection, deadline, clock)
                outcome, diagnostic = _outcome(record)
                if outcome != "CHILD_RUNNING":
                    outcome, diagnostic = _join_tail(
                        journal, record, connection, deadline, clock, sleep
                    )
                    break
                sleep(min(5, max(0, deadline - clock())))
            else:
                outcome, diagnostic = _outcome(record)
        except SubworkflowError as exc:
            record = journal.load()
            outcome, diagnostic = _observation_error_outcome(record, exc)
        _result(journal, record, outcome, diagnostic)
        return journal.load()


def reconcile(
    call_home: Path,
    call_key: str,
    *,
    client: Client | None = None,
    clock: Callable[[], float] = time.monotonic,
    _local_source: dict[str, object] | None = None,
    _deadline: float | None = None,
) -> dict[str, object]:
    """One bounded refresh; no public mutation or pending-cancel dispatch."""
    journal = Journal(call_home, call_key, _local_source=_local_source)
    with journal.lock(observer=True):
        record = journal.load()
        connection = _retained_client(record, client, clock)
        deadline = clock() + int(mapping(record["request"])["cleanup_seconds"])
        if _deadline is not None:
            deadline = min(deadline, _deadline)
        try:
            _refresh(journal, record, connection, deadline, clock)
            outcome, diagnostic = _outcome(record)
        except SubworkflowError as exc:
            record = journal.load()
            outcome, diagnostic = _observation_error_outcome(record, exc)
        _result(journal, record, outcome, diagnostic)
        return journal.load()


def advance(
    request_file: Path,
    *,
    expected_home: str | None = None,
    claim_sha256: str | None = None,
    cwd: Path | None = None,
    client: Client | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    """Advance this one call for a finite window; expiry never cancels its child."""
    try:
        with request_file.open("rb") as stream:
            raw = stream.read(1048577)
    except OSError as exc:
        raise SubworkflowError("request_unreadable") from exc
    return _advance_bytes(
        raw,
        expected_home=expected_home,
        claim_sha256=claim_sha256,
        cwd=cwd,
        client=client,
        clock=clock,
        sleep=sleep,
    )


def _advance_bytes(
    raw: bytes,
    *,
    expected_home: str | None = None,
    claim_sha256: str | None = None,
    cwd: Path | None = None,
    client: Client | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    _local_source: dict[str, object] | None = None,
    _deadline: float | None = None,
) -> dict[str, object]:
    # Service callers pass the exact bytes already checked against their claim.
    request = parse_request(raw, cwd=canonical_path(str(cwd or Path.cwd())))
    deadline = clock() + int(request["observe_seconds"])
    if _deadline is not None:
        deadline = min(deadline, _deadline)
    # Home comes only from pinned profile bytes. Service callers supply a check and
    # their verified claim digest; neither can change an existing call binding.
    profile = load_profile(request["os_profile"], home=expected_home)
    os_home = str(profile["os_home"])
    connection = client or WorkflowOSClient(request["os_profile"], os_home, clock=clock)
    journal = Journal(
        Path(str(request["call_home"])),
        str(request["call_key"]),
        _local_source=_local_source,
    )
    # Acquisition never blocks; the journal lock covers only atomic local updates.
    with journal.lock(observer=True):
        record = _prepare(
            journal,
            request,
            raw,
            os_home=os_home,
            claim_sha256=claim_sha256,
            client=connection,
            deadline=deadline,
        )
        if record["cancel_intent"] is None:
            _copy(journal, record)
        try:
            while clock() < deadline:
                if record["cancel_intent"] is not None:
                    _refresh(journal, record, connection, deadline, clock, sleep)
                    _pending_cancels(journal, record, connection, deadline, clock)
                    outcome, diagnostic = _outcome(record)
                    if outcome != "CHILD_RUNNING":
                        outcome, diagnostic = _join_tail(
                            journal, record, connection, deadline, clock, sleep
                        )
                        return _result(journal, record, outcome, diagnostic)
                    sleep(min(5, max(0, deadline - clock())))
                    continue
                if record["instance_id"] is None:
                    state = mapping(record["reservations"])["create"]
                    if state == "unsent":
                        _dispatch(journal, record, "create", connection, deadline)
                    else:
                        try:
                            _refresh(
                                journal, record, connection, deadline, clock, sleep
                            )
                        except SubworkflowError as exc:
                            if exc.code not in {"os_unavailable", "os_http_404"}:
                                raise
                            # Replay the saved create without allocating another ID.
                            _dispatch(journal, record, "create", connection, deadline)
                if (
                    record["instance_id"] is not None
                    and mapping(record["envelopes"])["start"] is None
                ):
                    mapping(record["envelopes"])["start"] = envelope(
                        "start_run",
                        request,
                        instance_id=str(record["instance_id"]),
                        expected=1,
                        payload={"edit_mode": "locked"},
                    )
                    _save(journal, record)
                outcome, diagnostic = _outcome(record)
                # A saved pause is not current: the refresh below decides it.
                if outcome not in {"CHILD_RUNNING", "CHILD_ATTENTION"}:
                    outcome, diagnostic = _join_tail(
                        journal, record, connection, deadline, clock, sleep
                    )
                    return _result(journal, record, outcome, diagnostic)
                if (
                    mapping(record["envelopes"])["start"] is not None
                    and mapping(record["reservations"])["start"] == "unsent"
                    and clock() < deadline
                ):
                    _dispatch(journal, record, "start", connection, deadline)
                if clock() < deadline:
                    _refresh(journal, record, connection, deadline, clock, sleep)
                outcome, diagnostic = _outcome(record)
                if outcome != "CHILD_RUNNING":
                    outcome, diagnostic = _join_tail(
                        journal, record, connection, deadline, clock, sleep
                    )
                    return _result(journal, record, outcome, diagnostic)
                remaining = deadline - clock()
                if remaining > 0:
                    sleep(min(5, remaining))
            return _result(journal, record, "CHILD_RUNNING", None)
        except SubworkflowError as exc:
            # Reload last durable evidence if a malformed/oversized update was refused.
            record = journal.load()
            outcome, diagnostic = _observation_error_outcome(record, exc)
            return _result(journal, record, outcome, diagnostic)
