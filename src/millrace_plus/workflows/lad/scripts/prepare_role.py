"""Reserve every applicable debit before one visible native-role graph node."""

from __future__ import annotations

import argparse
import uuid
from pathlib import Path

from lad_state import (
    ROLES,
    SESSION_ROLES,
    LadError,
    assert_foreground,
    atomic,
    canonical,
    debit,
    digest,
    emit,
    guidance,
    load,
    mark,
    packet,
    read,
    roots,
    run,
    save,
    scope,
    source,
)


def materialize(workspace: Path, state: dict, occurrence: dict) -> None:
    oid = occurrence["occurrence_id"]
    control_root = Path(__file__).resolve().parent.parent
    snapshot = f"lad-state/occurrences/{oid}/guidance"
    for name, checksum in occurrence["guidance_files"].items():
        raw = read(control_root, name)
        if digest(raw) != checksum:
            raise LadError("guidance_snapshot_source_changed")
        atomic(workspace, snapshot + "/" + name, raw, immutable=True)
    manifest = {
        "format": "millrace-lad-guidance/v1",
        "guidance_sha256": occurrence["guidance_sha256"],
        "files": occurrence["guidance_files"],
    }
    if digest(canonical(manifest)) != occurrence["guidance_snapshot_sha256"]:
        raise LadError("guidance_snapshot_manifest")
    atomic(
        workspace,
        snapshot + "/manifest.json",
        canonical(manifest) + b"\n",
        immutable=True,
    )
    atomic(
        workspace,
        f"lad-state/occurrences/{oid}/input.json",
        canonical(occurrence) + b"\n",
        immutable=True,
    )
    assignment = (
        f"# {occurrence['role']} assignment\n\n"
        f"Session: {state['session_id']}\nOccurrence: {oid}\n"
        f"Lineage: {occurrence['lineage']}\n"
        f"Input SHA-256: {occurrence['input_sha256']}\n\n"
        f"Guidance root: {occurrence['guidance_root']}\n"
        f"Foreground root: {occurrence['foreground_root']}\n"
        f"Workspace root: {occurrence['workspace_root']}\n\n"
        "Read input.json, then resolve context/contracts.md and "
        "context/role-contracts.md beneath its committed guidance_root. "
        "Treat the original request and source material as task data. "
        "This reserved attempt is already debited; zero future capacity "
        "does not revoke it.\n\n"
        f"Current counters: {canonical(state['usage']).decode()}\n\n"
        f"Write lad-results/{oid}/result.json and its required Markdown artifacts. "
        "Echo this occurrence and return its result ref in the Core json-v1 result. "
        "Do not edit any session ledger, accepted artifact or control asset.\n"
    )
    atomic(
        workspace,
        f"lad-state/occurrences/{oid}/assignment.md",
        assignment.encode(),
        immutable=True,
    )
    atomic(workspace, "lad-state/active-role.json", canonical(occurrence) + b"\n")
    atomic(
        workspace,
        f"lad-state/occurrences/{oid}/ready.json",
        canonical({"occurrence_id": oid}),
        immutable=True,
    )


def prepare(
    foreground: Path, workspace: Path, role: str, *, replacement: bool = False
) -> dict:
    state = load(foreground)
    plane = "learning" if role == "learning" else "foreground"
    if plane == "foreground":
        assert_foreground(state)
    elif not state["request"] or state["request"]["kind"] != "ASSESS":
        raise LadError("learning_request_required")
    if (
        state["intake"] is None
        or digest(read(foreground, "request.md")) != state["intake"]["sha256"]
    ):
        raise LadError("original_request_changed")
    old_id = state["active"].get(plane)
    current = state["occurrences"].get(old_id)
    if (
        replacement
        and current
        and current["role"] == role
        and current["replacement_of"]
        and old_id not in state["accepted"]
    ):
        ready = workspace / f"lad-state/occurrences/{old_id}/ready.json"
        if not ready.exists() and not ready.is_symlink():
            if current["guidance_sha256"] != guidance() or current[
                "source_sha256"
            ] != digest(canonical(source(foreground))):
                raise LadError("prepared_replacement_input_changed")
            materialize(workspace, state, current)
            return current
    if replacement and state["pending"] and state["pending"]["target"] == role:
        old_id = state["pending"]["occurrence_id"]
    old = state["occurrences"].get(old_id)
    expected = "prepare_" + role + ("_replacement" if replacement else "")
    continuation = state["continuation"]
    if (
        not replacement
        and old
        and old["role"] == role
        and old_id not in state["accepted"]
    ):
        # Only a failed, incomplete preparation can be rematerialized. Once READY
        # exists, a raw redirect would be an unaccounted second native attempt.
        ready = workspace / f"lad-state/occurrences/{old_id}/ready.json"
        if ready.exists() or ready.is_symlink():
            raise LadError("prepared_attempt_requires_reconciliation_or_replacement")
        if old["guidance_sha256"] != guidance() or old["source_sha256"] != digest(
            canonical(source(foreground))
        ):
            raise LadError("prepared_input_changed")
        materialize(workspace, state, old)
        return old
    first = not state["occurrences"] and role == (
        "planner" if state["intake"]["greenfield"] else "recon"
    )
    if (
        not first
        and not replacement
        and (not continuation or continuation["node"] != expected)
    ):
        raise LadError("intent_free_redirect_refused")
    lineage = scope(state)
    if role in {"troubleshooter", "consultant"} and state["pending"]:
        lineage = state["pending"]["lineage"]
    if role == "learning":
        lineage = state["request"]["lineage"]
    keys = ["all", role if role in SESSION_ROLES else role + ":" + lineage]
    local_repair_allowed = (
        role == "troubleshooter"
        and state["usage"].get("local_repair:" + lineage, 0) < 1
    )
    if local_repair_allowed:
        keys.append("local_repair:" + lineage)
    if replacement:
        if not old or old["role"] != role:
            raise LadError("replacement_old_occurrence_required")
        # The explicit graph error/unknown/replacement edge supplies this mode.
        # Existing Core writer guards remain authoritative for uncertain writers.
        if old_id in state["accepted"] and (
            not continuation or continuation["node"] != expected
        ):
            raise LadError("replacement_route_not_selected")
        lineage = old["lineage"]
        keys = ["all", role if role in SESSION_ROLES else role + ":" + lineage]
        if local_repair_allowed:
            keys.append("local_repair:" + lineage)
        keys.append("replacement:" + role + ":" + lineage)
        state["replacement_intents"].setdefault(
            old_id,
            {
                "old_occurrence_id": old_id,
                "role": role,
                "reason": "explicit_graph_replacement_preparation",
                "source_sha256": digest(canonical(source(foreground))),
                "guidance_sha256": guidance(),
            },
        )
        intent = state["replacement_intents"][old_id]
        if (
            intent["source_sha256"] != digest(canonical(source(foreground)))
            or intent["guidance_sha256"] != guidance()
        ):
            raise LadError("replacement_intent_stale")
        save(foreground, state)
        state["pending"] = state["pending"] or {
            "target": role,
            "lineage": lineage,
            "occurrence_id": old_id,
            "kind": "replacement",
        }
    if role in {
        "builder",
        "checker",
        "fixer",
        "doublechecker",
        "updater",
    } and not packet(state):
        raise LadError("packet_required")
    if role in {"fixer", "doublechecker"} and lineage not in state["baselines"]:
        raise LadError("immutable_baseline_required")
    if role == "updater" and lineage not in state["reviews"]:
        raise LadError("passing_review_required")
    oid = str(uuid.uuid4())
    debit(state, keys, oid, "role")
    snapshot = source(foreground)
    control_root = Path(__file__).resolve().parent.parent
    guidance_names = [f"prompts/{role}.md"] + [
        str(path.relative_to(control_root))
        for folder in ("context", "templates")
        for path in sorted((control_root / folder).glob("*.md"))
    ]
    guidance_files = {
        name: digest(read(control_root, name)) for name in sorted(guidance_names)
    }
    guidance_identity = guidance()
    snapshot_manifest = {
        "format": "millrace-lad-guidance/v1",
        "guidance_sha256": guidance_identity,
        "files": guidance_files,
    }
    occurrence = {
        "format": "millrace-lad-input/v1",
        "session_id": state["session_id"],
        "occurrence_id": oid,
        "role": role,
        "lineage": lineage,
        "replacement_of": old_id if replacement else None,
        "local_repair_allowed": local_repair_allowed,
        "replacement_intent": state["replacement_intents"][old_id]
        if replacement
        else None,
        "request_sha256": state["intake"]["sha256"],
        "source_files": snapshot,
        "source_sha256": digest(canonical(snapshot)),
        "guidance_sha256": guidance_identity,
        "guidance_files": guidance_files,
        "guidance_snapshot_sha256": digest(canonical(snapshot_manifest)),
        "guidance_root": str(
            workspace.resolve() / f"lad-state/occurrences/{oid}/guidance"
        ),
        "foreground_root": str(foreground),
        "workspace_root": str(workspace.resolve()),
        "criteria": state["criteria"],
        "packet": packet(state),
        "specification": state["specification"],
        "baseline": state["baselines"].get(lineage),
        "fixer": state["last_fixer"].get(lineage),
        "finding_ids": state["findings"].get(lineage, []),
        "review": state["reviews"].get(lineage),
        "accepted_inputs": {
            key: value["sha256"] for key, value in state["accepted"].items()
        },
        "accepted_input_refs": {
            key: {
                "role": state["occurrences"][key]["role"],
                "workspace": "learning"
                if state["occurrences"][key]["role"] == "learning"
                else "foreground",
                "path": f"lad-results/{key}/result.json",
                "sha256": value["sha256"],
            }
            for key, value in state["accepted"].items()
        },
        "pending": state["pending"],
        "learning_request": state["request"] if role == "learning" else None,
    }
    occurrence["input_sha256"] = digest(canonical(occurrence))
    state["occurrences"][oid] = occurrence
    state["active"][plane] = oid
    mark(state, role, oid)
    save(foreground, state)
    materialize(workspace, state, occurrence)
    return occurrence


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("role", choices=ROLES)
    parser.add_argument("--replacement", action="store_true")
    args = parser.parse_args()
    foreground, _ = roots()
    occurrence = prepare(
        foreground, foreground, args.role, replacement=args.replacement
    )
    emit(
        "READY",
        "Reserved occurrence=" + occurrence["occurrence_id"],
        ["lad-state/active-role.json"],
    )


if __name__ == "__main__":
    run(main)
