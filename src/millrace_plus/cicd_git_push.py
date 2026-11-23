"""Exact-ref push to an explicitly qualified, owned local Git receiver."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import cicd_v2_records as vr
from .cicd_git_commit import _git_file, _remaining
from .cicd_source import write_exact
from .local_intake import IntakeError, canonical, strict_json

BASIS = "millrace-plus-cicd-push-basis/v1"
CAPABILITY = "millrace-plus-cicd-local-push-capability/v1"
OBSERVATION = "millrace-plus-cicd-push-observation/v1"
GUARD_RECEIPT = "millrace-b0-guard-receipt/v1"
DISPLACEMENTS = {"push_superseded", "push_ref_moved"}
UNCERTAIN = "push_inspection_uncertain"
_PATH = re.compile(r"/[A-Za-z0-9_./-]+")
_REMOTE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_LOG = re.compile(
    rb"([0-9a-f]{40}|[0-9a-f]{64}) ([0-9a-f]{40}|[0-9a-f]{64}) "
    rb".+ <[^<>]+> [0-9]+ [+-][0-9]{4}\t[^\n]*\n"
)
_ENV = {
    "PATH": os.defpath,
    "LC_ALL": "C",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_NO_LAZY_FETCH": "1",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_ALLOW_PROTOCOL": "file",
    "GIT_TERMINAL_PROMPT": "0",
}


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def validate_selector(value: object, code: str) -> dict[str, Any]:
    obj = vr._fields(value, ("remote", "branch"), code)
    if not _REMOTE.fullmatch(vr._text(obj["remote"], code)):
        raise vr.CicdV2Error(code)
    branch = vr._text(obj["branch"], code)
    name = branch.removeprefix("refs/heads/")
    if (
        branch == name
        or not name
        or any(
            not part or part.startswith(".") or part.endswith(".lock")
            for part in name.split("/")
        )
        or branch.endswith(".")
        or ".." in branch
        or "@{" in branch
        or re.search(r"[\x00-\x20\x7f~^:?*\[\\]", branch)
    ):
        raise vr.CicdV2Error(code)
    return obj


def _directory(path: Path) -> dict[str, Any]:
    info = path.stat(follow_symlinks=False)
    if path.resolve(strict=True) != path or not stat.S_ISDIR(info.st_mode):
        raise vr.CicdV2Error("push_directory_changed")
    return {"path": str(path), "device": info.st_dev, "inode": info.st_ino}


def capability(home: Any, target: dict[str, Any]) -> tuple[dict, dict]:
    code = "invalid_push_capability"
    capabilities = home.config["external_policy"]["adapters"]["push"]["capabilities"]
    ref, other = (row["evidence_ref"] for row in capabilities)
    if ref != other:
        raise vr.CicdV2Error(code)
    evidence = vr._record(
        home.read_ref(ref),
        ("format", "remote", "receiver", "scripts", "tools", "qualification_sha256"),
        CAPABILITY,
        code,
    )
    if evidence["remote"] != target["remote"]:
        raise vr.CicdV2Error("effect_target_mismatch")
    vr._sha(evidence["qualification_sha256"], code)
    receiver = vr._fields(evidence["receiver"], ("path", "device", "inode"), code)
    if not _PATH.fullmatch(vr._text(receiver["path"], code)):
        raise vr.CicdV2Error(code)
    vr._integer(receiver["device"], code, low=0)
    vr._positive(receiver["inode"], code)
    for rows in (evidence["scripts"], evidence["tools"]):
        if len(vr._list(rows, code)) != 3:
            raise vr.CicdV2Error(code)
        for row in rows:
            vr._fields(row, ("path", "resolved_path", "mode", "sha256"), code)
            for key in ("path", "resolved_path"):
                if not _PATH.fullmatch(vr._text(row[key], code)):
                    raise vr.CicdV2Error(code)
            vr._integer(row["mode"], code, low=0)
            vr._sha(row["sha256"], code)
    return ref, evidence


def _route(home: Any, target: dict[str, Any]) -> tuple[Path, str, str]:
    _, evidence = capability(home, target)
    receiver = Path(evidence["receiver"]["path"])
    entry = Path(evidence["scripts"][0]["path"])
    guard = entry.parent / "hooks" / "pre-receive"
    scripts = [entry, guard, entry.parent / "receiver.py"]
    route_file = receiver.parent / "route.json"
    if (
        receiver == home.target
        or receiver.is_relative_to(home.target)
        or home.target.is_relative_to(receiver)
        or _directory(receiver) != evidence["receiver"]
        or entry.name != "receive-pack"
        or [str(path) for path in scripts]
        != [row["path"] for row in evidence["scripts"]]
        or any(
            path.is_relative_to(home.target) or path.is_relative_to(receiver)
            for path in (*scripts, route_file)
        )
    ):
        raise vr.CicdV2Error("push_receiver_changed")
    if route_file.lstat().st_mode & 0o222:
        raise vr.CicdV2Error("push_receiver_changed")
    expected = {
        key: evidence[key] for key in ("remote", "receiver", "scripts", "tools")
    }
    expected["workspace"] = _directory(home.workspace)
    if _git_file(route_file) != canonical(expected):
        raise vr.CicdV2Error("push_receiver_changed")
    for row in evidence["scripts"] + evidence["tools"]:
        path = Path(row["path"])
        resolved = path.resolve(strict=True)
        info = resolved.stat()
        if (
            str(resolved) != row["resolved_path"]
            or not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != row["mode"]
            or _sha(resolved.read_bytes()) != row["sha256"]
            or (path in scripts and (path != resolved or info.st_mode & 0o222))
        ):
            raise vr.CicdV2Error("push_receiver_changed")
    if not os.access(entry, os.X_OK) or not os.access(guard, os.X_OK):
        raise vr.CicdV2Error("push_receiver_changed")
    return receiver, str(entry), evidence["tools"][0]["path"]


def _git(argv: list[str], deadline: float, *, intent: dict | None = None):
    try:
        return subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            env=_ENV | (intent or {}),
            timeout=_remaining(deadline),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise vr.CicdV2Error("push_git_command_failed") from exc


def _read_git(argv: list[str], deadline: float, code: str) -> bytes:
    result = _git(argv, deadline)
    if result.returncode != 0:
        raise vr.CicdV2Error(code)
    return result.stdout


def _tip(git: str, receiver: Path, branch: str, deadline: float) -> str:
    code = "push_receiver_tip_unproven"
    argv = [git, "--git-dir=" + str(receiver)]
    if _git([*argv, "symbolic-ref", "-q", branch], deadline).returncode != 1:
        raise vr.CicdV2Error(code)
    raw = _read_git([*argv, "rev-parse", "--verify", branch], deadline, code)
    return vr._revision(raw.decode("ascii").strip(), code)


def _locked(receiver: Path, branch: str) -> bool:
    return any(
        os.path.lexists(receiver / name)
        for name in (branch + ".lock", "logs/" + branch + ".lock", "packed-refs.lock")
    )


def _log(receiver: Path, branch: str) -> bytes:
    path = receiver / "logs" / branch
    if path.resolve(strict=True) != path:
        raise vr.CicdV2Error("push_receiver_log_unproven")
    return _git_file(path)


def _rows(raw: bytes) -> list[tuple[str, str]]:
    rows = []
    for line in raw.splitlines(keepends=True):
        match = _LOG.fullmatch(line)
        if match is None or len(match[1]) != len(match[2]):
            raise vr.CicdV2Error("push_receiver_log_unproven")
        rows.append((match[1].decode("ascii"), match[2].decode("ascii")))
    return rows


def _snapshot(
    git: str, receiver: Path, branch: str, deadline: float
) -> tuple[str, bytes]:
    if _locked(receiver, branch):
        raise vr.CicdV2Error("push_receiver_locked")
    tip = _tip(git, receiver, branch, deadline)
    raw = _log(receiver, branch)
    if (
        tip != _tip(git, receiver, branch, deadline)
        or raw != _log(receiver, branch)
        or _locked(receiver, branch)
    ):
        raise vr.CicdV2Error("push_receiver_observation_changed")
    return tip, raw


def capture(home: Any, target: dict[str, Any], deadline: float) -> dict[str, Any]:
    ref, evidence = capability(home, target)
    receiver, _, git = _route(home, target)
    argv = [git, "--git-dir=" + str(receiver)]
    code = "unsupported_push_receiver"
    for args, expected in (
        (["rev-parse", "--is-bare-repository"], b"true"),
        (["rev-parse", "--show-ref-format"], b"files"),
        (["config", "--bool", "--get", "core.logAllRefUpdates"], b"true"),
        (["config", "--bool", "--get", "receive.autogc"], b"false"),
    ):
        if _read_git([*argv, *args], deadline, code).strip() != expected:
            raise vr.CicdV2Error(code)
    branch = target["branch"]
    tip, log = _snapshot(git, receiver, branch, deadline)
    rows = _rows(log)
    if tip != target["expected_remote_head"] or not rows or rows[-1][1] != tip:
        raise vr.CicdV2Error("push_receiver_tip_mismatch")
    return {
        "receiver": evidence["receiver"],
        "capability_ref": ref,
        "receiver_config_sha256": _sha(_git_file(receiver / "config")),
        "reflog_prefix": {"length": len(log), "sha256": _sha(log)},
        "prefix_ref": home.retain_bytes(log, ".push-prefix.bin"),
    }


def validate_basis(home: Any, basis: dict[str, Any]) -> None:
    code = "invalid_push_basis"
    ref, evidence = capability(home, basis["target"])
    prefix = vr._fields(basis["reflog_prefix"], ("length", "sha256"), code)
    vr._positive(prefix["length"], code)
    vr._sha(prefix["sha256"], code)
    vr._sha(basis["receiver_config_sha256"], code)
    raw = _git_file(home.resolve_ref(basis["prefix_ref"]))
    rows = _rows(raw)
    if (
        basis["capability_ref"] != ref
        or basis["receiver"] != evidence["receiver"]
        or prefix != {"length": len(raw), "sha256": _sha(raw)}
        or not rows
        or rows[-1][1] != basis["target"]["expected_remote_head"]
    ):
        raise vr.CicdV2Error(code)


def _sender(home: Any, target: dict, receiver: Path, entry: str, git: str, deadline):
    argv = [git, "-C", str(home.target)]
    raw = _read_git(
        [*argv, "config", "-z", "--includes", "--list"],
        deadline,
        "push_sender_unproven",
    )
    for row in raw.split(b"\0"):
        key, _, value = row.partition(b"\n")
        if (
            key.startswith(b"url.")
            and key.rsplit(b".", 1)[-1] in (b"insteadof", b"pushinsteadof")
            or key.startswith(os.fsencode(f"remote.{receiver}."))
            or key == b"push.pushoption"
            and value
            or key == b"push.gpgsign"
            and value.lower() not in (b"false", b"no", b"off", b"0")
        ):
            raise vr.CicdV2Error("push_sender_config_conflict")
    old, new = target["expected_remote_head"], target["head_sha"]
    _read_git(
        [*argv, "merge-base", "--is-ancestor", old, new],
        deadline,
        "push_non_fast_forward",
    )
    return [
        *argv,
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "push.negotiate=false",
        "-c",
        "push.autoSetupRemote=false",
        "-c",
        "protocol.allow=never",
        "-c",
        "protocol.file.allow=always",
        "push",
        "--porcelain",
        "--no-verify",
        "--no-follow-tags",
        "--recurse-submodules=no",
        "--signed=false",
        f"--receive-pack={entry}",
        str(receiver),
        f"{new}:{target['branch']}",
    ]


def _guard_receipt(basis: dict, request: dict) -> dict:
    target = request["target"]
    return {
        "format": GUARD_RECEIPT,
        "effect_id": request["effect_id"],
        "execute_sha256": vr.digest(request | {"operation": "execute"}),
        "basis_ref": request["evidence_refs"][0],
        "receiver": basis["receiver"],
        "reflog_prefix": basis["reflog_prefix"],
        "update": [
            target["expected_remote_head"],
            target["head_sha"],
            target["branch"],
        ],
    }


def _disposition(basis, request, observation, prefix, suffix, receipt) -> tuple:
    """Classify the exact tuple from closed-writer reflog and admission evidence."""
    expected = _guard_receipt(basis, request)
    old, new, _ = expected["update"]
    tip = observation["actual_tip"]
    absent = ("not_applied", None, None, None)
    unknown = ("uncertain", UNCERTAIN, None, None)
    if (
        observation["receiver"] != basis["receiver"]
        or observation["ref_locked"] is not False
        or observation["writers_closed"] is not True
        or basis["reflog_prefix"] != {"length": len(prefix), "sha256": _sha(prefix)}
    ):
        return unknown
    try:
        original = _rows(prefix)
        rows = _rows(suffix)
    except vr.CicdV2Error:
        return unknown
    if receipt is not None and receipt != canonical(expected):
        return unknown
    head = old
    for prior, after in rows:
        if prior != head or len(after) != len(old) or not int(after, 16):
            return unknown
        head = after
    if not original or original[-1][1] != old or tip != head:
        return unknown
    transition = (old, new)
    if transition in rows:
        if rows[0] != transition or receipt is None or rows.count(transition) != 1:
            return unknown
        return (
            "applied",
            "push_superseded" if len(rows) > 1 else None,
            request["effect_id"],
            tip,
        )
    if rows:
        return ("not_applied", "push_ref_moved", None, None)
    return absent


def validate_result_fields(result: dict, request: dict) -> None:
    code = "invalid_push_result"
    status, diagnostic = result["status"], result["diagnostic"]
    if result["artifact_sha256"] is not None:
        raise vr.CicdV2Error(code)
    if status == "applied":
        if (
            result["remote_id"] != request["effect_id"]
            or diagnostic not in (None, "push_superseded")
            or diagnostic is None
            and result["observed_head"] != request["target"]["head_sha"]
        ):
            raise vr.CicdV2Error(code)
    elif result["remote_id"] is not None or result["observed_head"] is not None:
        raise vr.CicdV2Error(code)
    if (
        status == "not_applied"
        and diagnostic not in (None, "push_ref_moved")
        or diagnostic in DISPLACEMENTS
        and status not in {"applied", "not_applied"}
    ):
        raise vr.CicdV2Error(code)


def validate_evidence(home: Any, result, request, refs) -> None:
    if result["status"] not in {"applied", "not_applied"} and not refs:
        return
    code = "invalid_push_evidence"
    if len(refs) not in (4, 5):
        raise vr.CicdV2Error(code)
    observation = vr._record(
        home.read_ref(refs[1]),
        (
            "format",
            "receiver",
            "actual_tip",
            "ref_locked",
            "writers_closed",
            "prefix_ref",
            "suffix_ref",
            "guard_ref",
        ),
        OBSERVATION,
        code,
    )
    original = result["evidence_refs"]
    linked = [observation["prefix_ref"], observation["suffix_ref"]]
    if observation["guard_ref"] is not None:
        linked.append(observation["guard_ref"])
    if linked != original[2:]:
        raise vr.CicdV2Error(code)
    prefix, suffix, *receipt = [_git_file(home.resolve_ref(ref)) for ref in refs[2:]]
    basis = home.read_ref(request["evidence_refs"][0])
    if _disposition(
        basis, request, observation, prefix, suffix, receipt[0] if receipt else None
    ) != tuple(
        result[key] for key in ("status", "diagnostic", "remote_id", "observed_head")
    ):
        raise vr.CicdV2Error(code)


def _observe(home, basis, request, receiver, git, deadline):
    target = request["target"]
    branch = target["branch"]
    tip, raw = _snapshot(git, receiver, branch, deadline)
    length = basis["reflog_prefix"]["length"]
    prefix, suffix = raw[:length], raw[length:]
    receipt_path = receiver.parent / f"guard-{request['effect_id']}.json"
    receipt = None
    if os.path.lexists(receipt_path):
        if receipt_path.lstat().st_mode & 0o222:
            raise vr.CicdV2Error(UNCERTAIN)
        receipt = _git_file(receipt_path)
    observation = {
        "format": OBSERVATION,
        "receiver": _directory(receiver),
        "actual_tip": tip,
        "ref_locked": _locked(receiver, branch),
        # Effect admission also requires the command group and Core writer to stop.
        "writers_closed": True,
        "prefix_ref": home.retain_bytes(prefix, ".push-prefix.bin"),
        "suffix_ref": home.retain_bytes(suffix, ".push-suffix.bin"),
        "guard_ref": None
        if receipt is None
        else home.retain_bytes(receipt, ".push-guard.json"),
    }
    status, diagnostic, remote, head = _disposition(
        basis, request, observation, prefix, suffix, receipt
    )
    from .cicd_effects import EVIDENCE

    proof = home.retain(
        {
            "format": EVIDENCE,
            "effect_id": request["effect_id"],
            "intent_sha256": vr.digest(request | {"operation": "execute"}),
            "target_sha256": vr.digest(target),
            "status": status,
            "remote_id": remote,
            "observed_head": head,
        }
    )
    refs = [
        proof,
        home.retain(observation),
        observation["prefix_ref"],
        observation["suffix_ref"],
    ]
    if observation["guard_ref"] is not None:
        refs.append(observation["guard_ref"])
    return status, diagnostic, remote, head, refs


def _run(home, request, request_path, deadline):
    target = request["target"]
    receiver, entry, git = _route(home, target)
    basis = home.read_ref(request["evidence_refs"][0])
    if _sha(_git_file(receiver / "config")) != basis["receiver_config_sha256"]:
        raise vr.CicdV2Error("push_receiver_changed")
    operation = request["operation"]
    if operation != "inspect":
        argv = _sender(home, target, receiver, entry, git, deadline)
        tip, raw = _snapshot(git, receiver, target["branch"], deadline)
        if (
            tip != target["expected_remote_head"]
            or {"length": len(raw), "sha256": _sha(raw)} != basis["reflog_prefix"]
        ):
            raise vr.CicdV2Error("push_receiver_tip_mismatch")
        if operation == "prepare":
            return "prepared", None, None, None, []
        raw = _git_file(request_path)
        if raw != canonical(request) or request_path.stat().st_mode & 0o222:
            raise vr.CicdV2Error("push_request_binding_changed")
        result = _git(
            argv,
            deadline,
            intent={
                "MILLRACE_CI_PUSH_INTENT_PATH": str(request_path),
                "MILLRACE_CI_PUSH_INTENT_SHA256": _sha(raw),
            },
        )
        if result.returncode != 0:
            return "uncertain", "push_response_unavailable", None, None, []
    return _observe(home, basis, request, receiver, git, deadline)


def main(argv: list[str] | None = None) -> int:
    from . import cicd_effects as effects
    from .cicd_v2_runtime import Runtime, V2Home

    try:
        request_path, result_path = map(Path, sys.argv[1:] if argv is None else argv)
        home = V2Home(request_path.parents[2])
        request = effects.validate_request(strict_json(_git_file(request_path)))
        operation = request["operation"]
        with home.lock():
            state = home.load_state(request["input_id"])
            _, operations = effects.history(home, state)
            last = operations[-1]
            stem = request["effect_id"] + "-" + last["invocation"]["invocation_id"]
            if (
                request["kind"] != "push"
                or last["status"] != "reserved"
                or last["operation"] != operation
                or home.resolve_ref(last["request_ref"]) != request_path
                or result_path != home.workspace / (stem + ".effect-result.json")
                or result_path.exists()
                or last["invocation"]["pid"] != os.getppid()
            ):
                return 1
            try:
                if operation != "inspect":
                    effects.current_basis(
                        Runtime(home, home.home / "workflow"), state, last["invocation"]
                    )
                status, diagnostic, remote, head, evidence = _run(
                    home, request, request_path, last["invocation"]["deadline"]
                )
            except (
                OSError,
                ValueError,
                IntakeError,
                vr.CicdV2Error,
                subprocess.SubprocessError,
            ) as exc:
                status = "refused" if operation == "prepare" else "uncertain"
                diagnostic = getattr(exc, "code", UNCERTAIN)
                remote, head, evidence = None, None, []
            result = {
                "format": effects.RESULT,
                "delivery_id": request["delivery_id"],
                "effect_id": request["effect_id"],
                "request_sha256": vr.digest(request),
                "operation": operation,
                "status": status,
                "remote_id": remote,
                "observed_head": head,
                "artifact_sha256": None,
                "evidence_refs": evidence,
                "observed_at": datetime.now(UTC).isoformat(),
                "diagnostic": diagnostic,
            }
            write_exact(
                result_path, canonical(effects.validate_result(result, request))
            )
        return 0
    except (OSError, ValueError, KeyError, IndexError, vr.CicdV2Error, IntakeError):
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
