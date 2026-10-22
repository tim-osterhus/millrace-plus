"""Accept exactly one immutable role result before exposing its finite route."""

from __future__ import annotations

import argparse
from pathlib import Path

from boundaries import choose
from lad_state import (
    ARTIFACTS,
    ROLES,
    LadError,
    assert_foreground,
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
    text,
)


def validate(
    workspace: Path, foreground: Path, state: dict, occurrence: dict, expected: str
) -> tuple[dict, str]:
    oid = occurrence["occurrence_id"]
    result = fields(
        load_json(read(workspace, f"lad-results/{oid}/result.json")),
        {
            "format",
            "session_id",
            "occurrence_id",
            "role",
            "outcome",
            "lineage",
            "input_sha256",
            "source_sha256",
            "guidance_sha256",
            "artifacts",
            "findings",
            "human_evidence",
            "data",
        },
    )
    for key in (
        "session_id",
        "occurrence_id",
        "role",
        "lineage",
        "input_sha256",
        "guidance_sha256",
    ):
        if result[key] != occurrence[key]:
            raise LadError("result_identity_" + key)
    if (
        result["format"] != "millrace-lad-result/v1"
        or result["outcome"] != expected
        or guidance() != occurrence["guidance_sha256"]
    ):
        raise LadError("result_contract_or_stale_guidance")
    if digest(read(foreground, "request.md")) != occurrence["request_sha256"]:
        raise LadError("original_request_changed")
    current = source(foreground)
    if result["source_sha256"] != digest(canonical(current)):
        raise LadError("stale_source")
    writable = occurrence["role"] in {"builder", "fixer", "troubleshooter", "updater"}
    paths = occurrence["packet"]["paths"] if occurrence["packet"] and writable else []
    changed = {
        name
        for name in current.keys() | occurrence["source_files"].keys()
        if current.get(name) != occurrence["source_files"].get(name)
    }
    if any(
        not any(
            name == allowed or name.startswith(allowed.rstrip("/") + "/")
            for allowed in paths
        )
        for name in changed
    ):
        raise LadError("out_of_scope_source_change")
    artifacts = result["artifacts"]
    if (
        not isinstance(artifacts, list)
        or not artifacts
        or not isinstance(result["data"], dict)
        or not isinstance(result["findings"], list)
    ):
        raise LadError("missing_role_evidence")
    names = set()
    for artifact in artifacts:
        fields(artifact, {"path", "sha256"})
        name = text(artifact["path"])
        if name in names or not name.endswith(".md"):
            raise LadError("duplicate_or_nonmarkdown_artifact")
        names.add(name)
        raw = read(workspace, f"lad-results/{oid}/" + name)
        content = raw.decode("utf-8")
        if (
            digest(raw) != artifact["sha256"]
            or oid not in content
            or len(content.strip()) < 100
            or "## Evidence" not in content
            or "## Outcome" not in content
        ):
            raise LadError("incomplete_or_wrong_occurrence_artifact")
    required = set(ARTIFACTS[occurrence["role"]])
    if expected in {"ERROR", "HUMAN_BLOCKED", "ERROR_REPAIRABLE", "ERROR_REPLACEMENT"}:
        required.discard("baseline.md")
    if not required <= names:
        raise LadError("missing_required_artifact")
    ids = set()
    for finding in result["findings"]:
        fields(finding, {"id", "criterion", "path", "reproduction", "evidence"})
        if finding["id"] in ids or finding["criterion"] not in {
            c["id"] for c in state["criteria"]
        }:
            raise LadError("finding_identity")
        ids.add(finding["id"])
        for value in finding.values():
            text(value)
        if (
            occurrence["role"] in {"checker", "doublechecker"}
            and finding["criterion"] not in occurrence["packet"]["criteria"]
        ):
            raise LadError("finding_outside_packet_criteria")
    if expected == "FINDINGS_ACTIONABLE" and not ids:
        raise LadError("actionable_findings_required")
    if expected == "PASS" and result["findings"]:
        raise LadError("pass_with_unresolved_findings")
    if expected == "HUMAN_BLOCKED" or expected == "NO_SAFE_ROUTE":
        human = fields(
            result["human_evidence"],
            {"type", "obstacle", "attempted", "required_resolution"},
        )
        if human["type"] not in {
            "unavailable_input",
            "human_choice",
            "protected_work",
            "budget_exhausted",
            "diagnosis_exhausted",
        }:
            raise LadError("human_dependence_not_established")
        for value in human.values():
            text(value)
    elif result["human_evidence"] is not None:
        raise LadError("unexpected_human_evidence")
    if expected.startswith("ERROR") or expected == "ERROR":
        failure = fields(
            result["data"].get("failure"),
            {"operation", "attempted", "evidence", "prerequisite_resolved"},
        )
        for key in ("operation", "attempted", "evidence"):
            text(failure[key])
        if type(failure["prerequisite_resolved"]) is not bool:
            raise LadError("invalid_failure_evidence")
        if (
            expected in {"ERROR_REPLACEMENT", "ERROR_TRANSIENT_RESOLVED"}
            and not failure["prerequisite_resolved"]
        ):
            raise LadError("unresolved_replacement_prerequisite")
    return result, digest(canonical(result))


def accept(foreground: Path, workspace: Path, role: str, expected: str) -> str:
    state = load(foreground)
    plane = "learning" if role == "learning" else "foreground"
    if plane == "foreground":
        assert_foreground(state)
    oid = state["active"].get(plane)
    occurrence = state["occurrences"].get(oid)
    if not occurrence or occurrence["role"] != role:
        raise LadError("unknown_or_wrong_role_occurrence")
    result, checksum = validate(workspace, foreground, state, occurrence, expected)
    existing = state["accepted"].get(oid)
    if existing:
        if existing["sha256"] != checksum or existing["result"] != result:
            raise LadError("conflicting_accepted_result")
        return existing["route"]
    route = choose(state, occurrence, result, foreground)
    state["accepted"][oid] = {"sha256": checksum, "result": result, "route": route}
    mark(
        state,
        "prepare_" + route.lower()
        if not route.startswith("RESUME_")
        else "prepare_" + route[7:].lower() + "_replacement",
        oid,
    )
    if route in {"ASSESS", "CLOSE", "ACK", "PAUSE", "REPLACE_LEARNING"}:
        mark(
            state,
            {
                "ASSESS": "emit_learning_request",
                "CLOSE": "emit_learning_request",
                "ACK": "emit_ack",
                "PAUSE": "human_pause",
                "REPLACE_LEARNING": "prepare_learning_replacement",
            }[route],
            oid,
        )
    if route == "REPLACE_CONSULTANT":
        mark(state, "prepare_consultant_replacement", oid)
    if route == "REPLACE_ARBITER":
        mark(state, "prepare_arbiter_replacement", oid)
    save(foreground, state)
    return route


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("role", choices=ROLES)
    parser.add_argument("outcome")
    args = parser.parse_args()
    if args.outcome not in ROLES[args.role]:
        raise LadError("undeclared_role_outcome")
    foreground, _ = roots()
    emit(
        accept(foreground, foreground, args.role, args.outcome),
        "Role evidence accepted once.",
    )


if __name__ == "__main__":
    run(main)
