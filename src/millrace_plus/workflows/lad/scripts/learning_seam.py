"""REQUEST/ACK domain artifacts over Core's two recurring seam envelopes."""

from __future__ import annotations

import argparse
import os
import uuid
from pathlib import Path

from lad_state import (
    CAPS,
    SESSION_ROLES,
    LadError,
    atomic,
    canonical,
    digest,
    emit,
    fields,
    guidance,
    load,
    load_json,
    mark,
    read,
    roots,
    run,
    save,
    source,
)


def seam_input(kind: str) -> tuple[dict, Path, dict]:
    raw_path = os.environ.get("MILLRACE_SEAM_INPUT", "")
    path = Path(raw_path)
    if (
        not path.is_absolute()
        or path != path.resolve()
        or path.name != "seam-input.json"
    ):
        raise LadError("actual_seam_input_required")
    envelope = fields(
        load_json(read(path.parent, path.name)),
        {
            "format",
            "run_id",
            "consumer_plane_id",
            "consumer_node_id",
            "seam_id",
            "sequence",
            "producer_plane_id",
            "producer_node_id",
            "producer_invocation_id",
            "producing_revision",
            "completion_id",
            "producer_workspace",
            "result",
        },
    )
    learning = kind == "request"
    expected = (
        (
            "learning_request",
            "foreground",
            "emit_learning_request",
            "learning",
            "receive_request",
            "REQUEST",
        )
        if learning
        else (
            "learning_ack",
            "learning",
            "emit_ack",
            "foreground",
            "accept_learning_ack",
            "ACK",
        )
    )
    actual = tuple(
        envelope[key]
        for key in (
            "seam_id",
            "producer_plane_id",
            "producer_node_id",
            "consumer_plane_id",
            "consumer_node_id",
        )
    ) + (envelope["result"].get("outcome"),)
    if (
        envelope["format"] != "millrace-seam-input/v1"
        or actual != expected
        or type(envelope["sequence"]) is not int
        or envelope["sequence"] < 1
    ):
        raise LadError("wrong_seam_identity")
    for key in (
        "run_id",
        "producer_invocation_id",
        "completion_id",
        "producing_revision",
    ):
        value = envelope[key]
        size = 64 if key == "producing_revision" else 32
        if (
            not isinstance(value, str)
            or len(value) != size
            or any(char not in "0123456789abcdef" for char in value)
        ):
            raise LadError("invalid_core_seam_identity")
    workspace = Path.cwd().resolve()
    if workspace.name != ("learning" if learning else "foreground"):
        raise LadError("wrong_consumer_workspace")
    producer = workspace.parent / ("foreground" if learning else "learning")
    if envelope["producer_workspace"] != str(producer):
        raise LadError("wrong_producer_workspace")
    refs = envelope["result"].get("refs")
    if not isinstance(refs, list) or len(refs) != 1:
        raise LadError("one_pinned_seam_artifact_required")
    raw = read(producer, refs[0])
    if envelope["result"].get("summary") != "sha256=" + digest(raw):
        raise LadError("seam_artifact_digest")
    return envelope, producer, load_json(raw)


def emit_request() -> None:
    foreground, _ = roots()
    state = load(foreground)
    if state["request"]:
        request = state["request"]
        if request["stage"] != "published":
            raise LadError("request_already_consumed_recover_original_run")
    else:
        kind = "CLOSE" if state["terminal"] else "ASSESS"
        pending = state["pending"]
        if kind == "ASSESS" and (
            not pending
            or pending["kind"] != "learning_gate"
            or len(pending["pairs"]) != 2
        ):
            raise LadError("two_evaluated_pairs_required")
        rid = str(uuid.uuid4())
        payload = {
            "format": "millrace-lad-learning-request/v1",
            "request_id": rid,
            "session_id": state["session_id"],
            "kind": kind,
            "lineage": pending["lineage"] if pending else "unpacketized",
            "occurrence_id": pending["occurrence_id"]
            if pending
            else state["continuation"]["occurrence_id"],
            "pending_route": pending,
            "terminal": state["terminal"],
            "source_sha256": digest(canonical(source(foreground))),
            "guidance_sha256": guidance(),
        }
        name = "lad-state/seams/request-" + rid + ".json"
        raw = canonical(payload) + b"\n"
        atomic(foreground, name, raw, immutable=True)
        request = {
            "id": rid,
            "kind": kind,
            "lineage": payload["lineage"],
            "path": name,
            "sha256": digest(raw),
            "payload": payload,
            "stage": "published",
            "core": None,
        }
        state["request"] = request
        mark(state, "accept_learning_ack", payload["occurrence_id"])
        save(foreground, state)
    emit("REQUEST", "sha256=" + request["sha256"], [request["path"]])


def receive() -> None:
    envelope, foreground, payload = seam_input("request")
    state = load(foreground)
    request = state["request"]
    if (
        not request
        or request["payload"] != payload
        or request["sha256"] != digest(read(foreground, request["path"]))
        or payload["session_id"] != state["session_id"]
    ):
        raise LadError("stale_or_wrong_learning_request")
    core = {
        key: envelope[key]
        for key in (
            "run_id",
            "sequence",
            "producer_invocation_id",
            "producing_revision",
            "completion_id",
        )
    }
    if request["core"] not in (None, core):
        raise LadError("cross_run_or_conflicting_request")
    previous = state["seam_seen"].get("request", 0)
    if request["stage"] == "published" and envelope["sequence"] != previous + 1:
        raise LadError("request_sequence")
    request["core"] = core
    request["stage"] = "received"
    state["seam_seen"]["request"] = envelope["sequence"]
    if payload["kind"] == "ASSESS":
        mark(state, "prepare_learning", payload["occurrence_id"])
    save(foreground, state)
    atomic(Path.cwd(), "lad-state/current-request.json", canonical(request) + b"\n")
    emit(payload["kind"], "Committed request=" + payload["request_id"])


def selected() -> tuple[Path, dict, dict]:
    workspace = Path.cwd().resolve()
    if workspace.name != "learning":
        raise LadError("learning_workspace_required")
    foreground = workspace.parent / "foreground"
    projection = load_json(read(workspace, "lad-state/current-request.json"))
    state = load(foreground)
    request = state["request"]
    if (
        not request
        or request["id"] != projection["id"]
        or request["core"] != projection["core"]
        or request["payload"] != projection["payload"]
    ):
        raise LadError("retained_request_mismatch")
    return foreground, state, request


def learning_prepare(replacement: bool) -> None:
    from prepare_role import prepare

    foreground, state, request = selected()
    lineage = request["lineage"]
    if replacement and (
        state["usage"].get("learning:" + lineage, 0) >= 2
        or state["usage"].get("replacement:learning:" + lineage, 0) >= 1
    ):
        # A dispatched failed/unknown attempt stays spent; no third attempt.
        state["learning"][lineage] = {
            "disposition": "failed",
            "occurrence_id": state["active"].get("learning"),
        }
        mark(state, "emit_ack")
        save(foreground, state)
        emit("ACK", "Learning attempts exhausted; preserving legal pending route.")
        return
    occurrence = prepare(foreground, Path.cwd(), "learning", replacement=replacement)
    emit("READY", "Reserved Learning=" + occurrence["occurrence_id"])


def learning_accept(outcome: str) -> None:
    from accept_role import accept

    foreground, _, _ = selected()
    emit(
        accept(foreground, Path.cwd(), "learning", outcome),
        "Learning accepted before ACK.",
    )


def legal_pending(state: dict, request: dict, foreground: Path) -> None:
    payload = request["payload"]
    if (
        payload["source_sha256"] != digest(canonical(source(foreground)))
        or payload["guidance_sha256"] != guidance()
        or payload["pending_route"] != state["pending"]
    ):
        raise LadError("saved_route_authority_changed")
    if request["kind"] == "ASSESS":
        target = state["pending"]["target"]
        if target not in {"updater", "troubleshooter"}:
            raise LadError("illegal_pending_route")
        key = target if target in SESSION_ROLES else target + ":" + request["lineage"]
        if (
            state["usage"].get("all", 0) >= CAPS["all"]
            or state["usage"].get(key, 0) >= CAPS[target]
        ):
            raise LadError("saved_route_budget_exhausted")


def emit_ack() -> None:
    foreground, state, request = selected()
    legal_pending(state, request, foreground)
    if request["kind"] == "ASSESS" and request["lineage"] not in state["learning"]:
        raise LadError("learning_disposition_required")
    ack = {
        "format": "millrace-lad-learning-ack/v1",
        "session_id": state["session_id"],
        "request_id": request["id"],
        "request_sha256": request["sha256"],
        "request_core": request["core"],
        "kind": request["kind"],
        "lineage": request["lineage"],
        "pending_route": request["payload"]["pending_route"],
        "source_sha256": request["payload"]["source_sha256"],
        "guidance_sha256": request["payload"]["guidance_sha256"],
        "terminal": request["payload"]["terminal"],
        "learning": state["learning"].get(request["lineage"])
        if request["kind"] == "ASSESS"
        else None,
    }
    name = "lad-state/seams/ack-" + request["id"] + ".json"
    raw = canonical(ack) + b"\n"
    atomic(Path.cwd(), name, raw, immutable=True)
    request["stage"] = "ack_ready"
    save(foreground, state)
    emit("ACK", "sha256=" + digest(raw), [name])


def accept_ack() -> None:
    foreground, _ = roots()
    envelope, _, ack = seam_input("ack")
    state = load(foreground)
    request = state["request"]
    if not request:
        prior = state["seam_seen"].get("accepted_ack")
        if prior and prior["ack"] == ack and prior["core"] == envelope:
            emit(prior["route"], "Exact ACK replay.")
            return
        raise LadError("no_pending_request")
    legal_pending(state, request, foreground)
    expected = {
        "format": "millrace-lad-learning-ack/v1",
        "session_id": state["session_id"],
        "request_id": request["id"],
        "request_sha256": request["sha256"],
        "request_core": request["core"],
        "kind": request["kind"],
        "lineage": request["lineage"],
        "pending_route": request["payload"]["pending_route"],
        "source_sha256": request["payload"]["source_sha256"],
        "guidance_sha256": request["payload"]["guidance_sha256"],
        "terminal": request["payload"]["terminal"],
        "learning": state["learning"].get(request["lineage"])
        if request["kind"] == "ASSESS"
        else None,
    }
    if (
        ack != expected
        or request["stage"] != "ack_ready"
        or envelope["sequence"] != request["core"]["sequence"]
        or envelope["run_id"] != request["core"]["run_id"]
    ):
        raise LadError("stale_wrong_or_conflicting_ack")
    route = (
        "FINALIZE" if request["kind"] == "CLOSE" else state["pending"]["target"].upper()
    )
    state["seam_seen"]["accepted_ack"] = {"ack": ack, "core": envelope, "route": route}
    if route == "TROUBLESHOOTER":
        pending = state["pending"]
        state["pending"] = {
            "kind": "review",
            "target": "doublechecker",
            "lineage": pending["lineage"],
            "occurrence_id": pending["occurrence_id"],
        }
    else:
        state["pending"] = None
    state["request"] = None
    mark(state, "finalize" if route == "FINALIZE" else "prepare_" + route.lower())
    save(foreground, state)
    emit(route, "Exact committed Learning acknowledgement accepted.")


def after_ack() -> None:
    # Read-only local projection: foreground may already own the ledger again.
    request = load_json(read(Path.cwd(), "lad-state/current-request.json"))
    emit(
        "CLOSE" if request["kind"] == "CLOSE" else "NEXT", "No ledger writes after ACK."
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "operation",
        choices=[
            "emit_request",
            "receive",
            "prepare",
            "replace",
            "accept",
            "emit_ack",
            "accept_ack",
            "after_ack",
        ],
    )
    parser.add_argument("outcome", nargs="?")
    args = parser.parse_args()
    if args.operation == "accept":
        learning_accept(args.outcome)
    elif args.operation in {"prepare", "replace"}:
        learning_prepare(args.operation == "replace")
    else:
        {
            "emit_request": emit_request,
            "receive": receive,
            "emit_ack": emit_ack,
            "accept_ack": accept_ack,
            "after_ack": after_ack,
        }[args.operation]()


if __name__ == "__main__":
    run(main)
