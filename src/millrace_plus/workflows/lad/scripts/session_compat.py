"""Frozen v1 interpretation and explicit domain upgrade; normal paths use stdlib."""

from __future__ import annotations

import uuid
from pathlib import Path

from lad_state import (
    LadError,
    atomic,
    canonical,
    digest,
    fields,
    load_json,
    read,
)

V1_KEYS = {'accepted',
 'active',
 'baselines',
 'continuation',
 'criteria',
 'events',
 'findings',
 'format',
 'identity_sha256',
 'inputs_sha256',
 'intake',
 'integrity_sha256',
 'last_fixer',
 'learning',
 'limits',
 'occurrences',
 'packet_index',
 'pairs',
 'pending',
 'plan',
 'replacement_intents',
 'request',
 'reviews',
 'seam_seen',
 'session_id',
 'specification',
 'terminal',
 'usage'}
LEGACY_CAPS = {'all': 60,
 'arbiter': 3,
 'builder': 4,
 'checker': 4,
 'consultant': 2,
 'doublechecker': 4,
 'fixer': 3,
 'learning': 2,
 'local_repair': 1,
 'manager': 3,
 'planner': 3,
 'planning_repair': 1,
 'recon': 3,
 'remediation': 1,
 'replacement': 1,
 'troubleshooter': 2,
 'updater': 4}

def validate_v1(
    foreground: Path, state: dict, *, active_projection: bool = True
) -> dict:
    expected = V1_KEYS
    fields(state, expected)
    content = dict(state)
    checksum = content.pop("integrity_sha256")
    if checksum != digest(canonical(content)):
        raise LadError("session_integrity")
    if state["format"] != "millrace-lad-session/v1" or state["limits"] != LEGACY_CAPS:
        raise LadError("session_contract")
    uuid.UUID(state["session_id"])
    for name in (
        "usage",
        "occurrences",
        "active",
        "replacement_intents",
        "accepted",
        "baselines",
        "pairs",
        "last_fixer",
        "findings",
        "reviews",
        "learning",
        "seam_seen",
    ):
        if not isinstance(state[name], dict):
            raise LadError("corrupt_ledger")
    if (
        not isinstance(state["events"], list)
        or not isinstance(state["plan"], list)
        or not isinstance(state["criteria"], list)
    ):
        raise LadError("corrupt_ledger")
    actual = {}
    reserved = set()
    for event in state["events"]:
        fields(event, {"id", "debits", "kind"})
        if event["kind"] not in {"role", "planning_repair", "remediation"}:
            raise LadError("unknown_debit_event")
        if (
            event["id"] in reserved
            or not isinstance(event["debits"], list)
            or not event["debits"]
        ):
            raise LadError("corrupt_debits")
        reserved.add(event["id"])
        for key in event["debits"]:
            cap = LEGACY_CAPS[key.split(":", 1)[0]]
            actual[key] = actual.get(key, 0) + 1
            if actual[key] > cap:
                raise LadError("corrupt_debits")
        if event["kind"] == "role" and event["id"] not in state["occurrences"]:
            raise LadError("missing_occurrence")
    if state["usage"] != actual:
        raise LadError("usage_mismatch")
    for oid, occurrence in state["occurrences"].items():
        if (
            oid != occurrence.get("occurrence_id")
            or occurrence.get("session_id") != state["session_id"]
            or oid not in reserved
        ):
            raise LadError("occurrence_mismatch")
        stored = dict(occurrence)
        checksum = stored.pop("input_sha256")
        if checksum != digest(canonical(stored)):
            raise LadError("occurrence_digest")
        workspace = (
            foreground
            if occurrence["role"] != "learning"
            else foreground.parent / "learning"
        )
        relative = f"lad-state/occurrences/{oid}/input.json"
        projection = workspace / relative
        ready = workspace / f"lad-state/occurrences/{oid}/ready.json"
        if projection.exists() or projection.is_symlink():
            if load_json(read(workspace, relative)) != occurrence:
                raise LadError("occurrence_projection_conflict")
        elif ready.exists() or ready.is_symlink():
            raise LadError("ready_occurrence_missing_input")
        if ready.exists() or ready.is_symlink() or oid in state["accepted"]:
            expected_root = workspace / f"lad-state/occurrences/{oid}/guidance"
            if (
                occurrence["guidance_root"] != str(expected_root)
                or not ready.is_file()
                or ready.is_symlink()
            ):
                raise LadError("guidance_locator_or_ready_conflict")
            manifest = {
                "format": "millrace-lad-guidance/v1",
                "guidance_sha256": occurrence["guidance_sha256"],
                "files": occurrence["guidance_files"],
            }
            manifest_relative = f"lad-state/occurrences/{oid}/guidance/manifest.json"
            if (
                load_json(read(workspace, manifest_relative)) != manifest
                or digest(canonical(manifest)) != occurrence["guidance_snapshot_sha256"]
            ):
                raise LadError("guidance_snapshot_manifest")
            for name, checksum in manifest["files"].items():
                if (
                    digest(
                        read(workspace, f"lad-state/occurrences/{oid}/guidance/" + name)
                    )
                    != checksum
                ):
                    raise LadError("guidance_snapshot_changed")
    for plane, oid in (state["active"].items() if active_projection else ()):
        if plane not in {"foreground", "learning"} or oid not in state["occurrences"]:
            raise LadError("active_mismatch")
        workspace = (
            foreground if plane == "foreground" else foreground.parent / "learning"
        )
        projection = workspace / "lad-state/active-role.json"
        if projection.exists() or projection.is_symlink():
            active = load_json(read(workspace, "lad-state/active-role.json"))
            if active != state["occurrences"][oid]:
                previous = active.get("occurrence_id")
                ready = workspace / f"lad-state/occurrences/{oid}/ready.json"
                allowed_previous = (
                    previous in state["accepted"]
                    or state["occurrences"][oid]["replacement_of"] == previous
                )
                if (
                    ready.exists()
                    or not allowed_previous
                    or active != state["occurrences"].get(previous)
                ):
                    raise LadError("active_projection_conflict")
    for oid, accepted in state["accepted"].items():
        occurrence = state["occurrences"].get(oid)
        if not occurrence or accepted.get("sha256") != digest(
            canonical(accepted.get("result"))
        ):
            raise LadError("accepted_digest")
        workspace = (
            foreground
            if occurrence["role"] != "learning"
            else foreground.parent / "learning"
        )
        raw = load_json(read(workspace, f"lad-results/{oid}/result.json"))
        if raw != accepted["result"]:
            raise LadError("accepted_output_changed")
        for artifact in raw["artifacts"]:
            if (
                digest(read(workspace, f"lad-results/{oid}/" + artifact["path"]))
                != artifact["sha256"]
            ):
                raise LadError("accepted_artifact_changed")
    if state["occurrences"] and not state["continuation"]:
        raise LadError("missing_continuation")
    return state


def source_v1(foreground: Path) -> dict[str, str]:
    from lad_state import safe

    target = safe(foreground, "target", missing=True)
    if not target.exists():
        return {}
    if not target.is_dir():
        raise LadError("target_directory_required")
    return {
        str(p.relative_to(target)): digest(read(target, str(p.relative_to(target))))
        for p in sorted(target.rglob("*"))
        if not p.is_dir() or p.is_symlink()
    }


def _stopped_snapshot(view: dict, instance_id: str, run_id: str) -> None:
    if (
        view["id"] != run_id or view["instance_id"] != instance_id
        or view["status"] not in {"paused", "succeeded", "failed", "cancelled"}
        or view["quiescent"] is not True or view["inflight_invocations"] != []
        or any(row["writer_state"] != "stopped" or row["status"] != "completed"
               for row in view["invocations"])
        or any(row["effective_writer"] != "stopped" for row in view["completions"])
    ):
        raise LadError("upgrade_stopped_exact_run_required")


def _original_seams(foreground: Path, state: dict, view: dict) -> None:
    request = state["request"]
    if request is None:
        return
    raw = read(foreground, request["path"])
    if digest(raw) != request["sha256"] or load_json(raw) != request["payload"]:
        raise LadError("upgrade_request_payload_changed")
    if (request["payload"]["session_id"] != state["session_id"]
        or request["payload"]["request_id"] != request["id"]
        or request["payload"]["pending_route"] != state["pending"]):
        raise LadError("upgrade_saved_route_changed")
    matches = [load_json(row["body"].encode()) for row in view["seam_occurrences"]
               if row["seam_id"] == "learning_request"]
    selected = [item for item in matches if item["result"] == {
        "outcome": "REQUEST", "summary": "sha256=" + request["sha256"],
        "refs": [request["path"]],
    }]
    if len(selected) != 1 or selected[0]["run_id"] != view["id"]:
        raise LadError("upgrade_original_request_run_required")
    envelope = selected[0]
    core = request["core"]
    if core is not None:
        fields(core, {"run_id", "sequence", "producer_invocation_id",
                      "producing_revision", "completion_id"})
        if core != {key: envelope[key] for key in core} or not any(
            load_json(row["body"].encode()) == envelope
            for row in view["seam_selections"] if row["seam_id"] == "learning_request"
        ):
            raise LadError("upgrade_original_request_selection_required")
    elif request["stage"] != "published":
        raise LadError("upgrade_request_core_missing")
    if request["stage"] == "ack_ready":
        relative = "lad-state/seams/ack-" + request["id"] + ".json"
        ack_raw = read(foreground.parent / "learning", relative)
        ack = load_json(ack_raw)
        expected = {
            "format": "millrace-lad-learning-ack/v1",
            "session_id": state["session_id"], "request_id": request["id"],
            "request_sha256": request["sha256"], "request_core": core,
            "kind": request["kind"], "lineage": request["lineage"],
            "pending_route": request["payload"]["pending_route"],
            "source_sha256": request["payload"]["source_sha256"],
            "guidance_sha256": request["payload"]["guidance_sha256"],
            "terminal": request["payload"]["terminal"],
            "learning": state["learning"].get(request["lineage"])
            if request["kind"] == "ASSESS" else None,
        }
        if ack != expected:
            raise LadError("upgrade_original_ack_payload_required")
        if not any(
            item["run_id"] == view["id"] and item["sequence"] == envelope["sequence"]
            and item["result"] == {
                "outcome": "ACK", "summary": "sha256=" + digest(ack_raw),
                "refs": [relative],
            }
            for item in (load_json(row["body"].encode())
                         for row in view["seam_occurrences"]
                         if row["seam_id"] == "learning_ack")
        ):
            raise LadError("upgrade_original_ack_run_required")


def _eligible_revision(
    public, instance_id: str, run_id: str, state_home: Path, view: dict
) -> None:
    draft = view["draft_revision"]
    if draft.get("diagnostic") is not None or not draft.get("revision_id"):
        raise LadError("upgrade_incompatible_authored_revision")
    validation = public.validate_revision(
        instance_id, state_home=state_home, run_id=run_id
    )
    context = validation.get("validation_context", {})
    if (validation.get("valid") is not True or validation.get("diagnostics") != []
        or context.get("kind") != "run" or context.get("instance_id") != instance_id
        or context.get("run_id") != run_id
        or context.get("run_state_version") != view["state_version"]
        or validation.get("draft_revision") != draft):
        raise LadError("upgrade_incompatible_authored_revision")


def upgrade_session(
    foreground: Path, *, session_id: str, instance_id: str, run_id: str,
    state_home: Path, expected_state_version: int, catalog_home: Path | None = None,
) -> str:
    """Publish within the caller's separately established maintenance window.

    Caller owns/joins controller/OS callers and excludes domain/helper and
    ownership-maintenance writers. Paused/quiescent and locks do not prove
    OS/helper joins. Actual used-copy operations require separate qualification.
    No mutator, process or helper call is made while the public context is held.
    """
    try:
        from millrace_plus.router import control as public

        guard = public.exclusive_run_snapshot
    except Exception as exc:
        raise LadError("upgrade_runtime_unavailable") from exc
    from lad_state import _validate_state, load

    try:
        with guard(instance_id, run_id, state_home=state_home,
                   expected_state_version=expected_state_version,
                   catalog_home=catalog_home) as view:
            _stopped_snapshot(view, instance_id, run_id)
            _eligible_revision(public, instance_id, run_id, state_home, view)
            identity = public.inspect_instance(instance_id, state_home=state_home)
            project = foreground.parent.resolve()
            if (Path(identity["project_root"]) != project
                or Path(identity["workflow_file"]) != project / "workflow.json"):
                raise LadError("upgrade_project_identity")
            before = read(foreground, "lad-state/session.json")
            state = load_json(before)
            if state["session_id"] != session_id:
                raise LadError("upgrade_session_identity")
            if state["format"] == "millrace-lad-session/v2":
                loaded = load(foreground)
                if loaded["legacy"] is None:
                    raise LadError("upgrade_requires_retained_v1")
                _original_seams(foreground, loaded, view)
                return "SESSION_ALREADY_UPGRADED"
            old = validate_v1(foreground, state)
            _original_seams(foreground, old, view)
            legacy = {"path": "lad-state/legacy/" + digest(before) + ".json",
                      "sha256": digest(before), "format": "millrace-lad-session/v1"}
            record = {
                "format": "millrace-lad-migration/v1", "session_id": session_id,
                "source": legacy, "destination_format": "millrace-lad-session/v2",
                "preserved_occurrence_ids": sorted(old["occurrences"]),
                "source_mappings": [
                    {"occurrence_id": oid, **{key: item[key] for key in (
                        "input_sha256", "source_sha256",
                        "guidance_sha256", "guidance_root"
                    )}, "requires_revalidation": True}
                    for oid, item in sorted(old["occurrences"].items())
                ],
            }
            record_raw = canonical(record) + b"\n"
            mapping_path = "lad-state/migrations/v1-to-v2.json"
            for relative, raw in ((legacy["path"], before), (mapping_path, record_raw)):
                path = foreground / relative
                if ((path.exists() or path.is_symlink())
                    and read(foreground, relative) != raw):
                    raise LadError("upgrade_immutable_conflict")
            converted = dict(old)
            converted.pop("limits")
            converted.pop("usage")
            converted["format"] = "millrace-lad-session/v2"
            converted["events"] = [{"id": row["id"], "kind": row["kind"]}
                                   for row in old["events"]]
            converted["legacy"] = legacy
            converted["migration"] = {
                "path": mapping_path, "sha256": digest(record_raw)
            }
            converted.pop("integrity_sha256")
            converted["integrity_sha256"] = digest(canonical(converted))
            _validate_state(foreground, converted, migration_data=(before, record_raw))
            atomic(foreground, legacy["path"], before, immutable=True)
            atomic(foreground, mapping_path, record_raw, immutable=True)
            _validate_state(foreground, converted)
            if read(foreground, "lad-state/session.json") != before:
                raise LadError("upgrade_source_session_changed")
            final_view = public.inspect_run(instance_id, run_id, state_home=state_home)
            _stopped_snapshot(final_view, instance_id, run_id)
            _eligible_revision(public, instance_id, run_id, state_home, final_view)
            if (final_view["state_version"] != view["state_version"]
                or final_view["draft_revision"] != view["draft_revision"]):
                raise LadError("upgrade_authored_revision_changed")
            atomic(foreground, "lad-state/session.json", canonical(converted) + b"\n")
            load(foreground)
            return "SESSION_UPGRADED"
    except LadError:
        raise
    except Exception as exc:
        code = getattr(exc, "code", None)
        diagnostic = code if isinstance(code, str) else "upgrade_public_facts_refused"
        raise LadError(diagnostic) from exc


def _materialize_v1(workspace: Path, state: dict, occurrence: dict) -> None:
    oid = occurrence["occurrence_id"]
    control_root = Path(__file__).resolve().parent.parent
    snapshot = f"lad-state/occurrences/{oid}/guidance"
    for name, checksum in occurrence["guidance_files"].items():
        candidates = [(workspace, snapshot + "/" + name), (control_root, name)]
        for previous in state["occurrences"].values():
            previous_workspace = (workspace.parent / "learning"
                                  if previous["role"] == "learning"
                                  else workspace.parent / "foreground")
            candidates.append((previous_workspace,
                               "lad-state/occurrences/"
                               + previous["occurrence_id"] + "/guidance/" + name))
        raw = None
        for root, relative in candidates:
            try:
                observed = read(root, relative)
            except (LadError, OSError):
                continue
            if digest(observed) == checksum:
                raw = observed
                break
        if raw is None:
            raise LadError("legacy_guidance_unavailable")
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


def restore_legacy(
    foreground: Path, workspace: Path, state: dict, occurrence: dict
) -> None:
    if state["legacy"] is None:
        raise LadError("legacy_mapping_required")
    old = validate_v1(foreground, load_json(read(foreground, state["legacy"]["path"])),
                      active_projection=False)
    if old["occurrences"].get(occurrence["occurrence_id"]) != occurrence:
        raise LadError("legacy_occurrence_changed")
    _materialize_v1(workspace, old, occurrence)
