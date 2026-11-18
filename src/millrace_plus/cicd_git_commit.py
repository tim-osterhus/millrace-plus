"""The opt-in standalone Git commit peer for CI's saved effect protocol."""

from __future__ import annotations

import hashlib
import os
import re
import select
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import check_contracts as cc
from . import cicd_v2_records as vr
from .cicd_source import source_identity, write_exact
from .local_intake import canonical

PLAN = "millrace-plus-cicd-local-git-plan/v1"
REFLOG_MESSAGE = "millrace: approved CI repair"
OID = re.compile(rb"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")
IDENT = re.compile(r"[^<>\x00-\x1f\x7f]+\Z")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _remaining(deadline: float) -> float:
    remaining = deadline - time.time()
    if remaining <= 0:
        raise vr.CicdV2Error("invocation_deadline_expired")
    return remaining


def _git_file(path: Path, *, missing: bytes | None = None) -> bytes:
    from .cicd_v2_runtime import _read

    # Avoid opening a FIFO or device when checking Git metadata.
    try:
        info = path.lstat()
    except FileNotFoundError:
        if missing is not None:
            return missing
        raise
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise vr.CicdV2Error("unsafe_git_repository")
    return _read(path)


class Git:
    """Use fixed local plumbing, with explicit roots and no inherited Git options."""

    def __init__(self, target: Path, branch: str, deadline: float) -> None:
        self.target = target
        self.metadata = target / ".git"
        self.branch = "refs/heads/" + branch
        self.deadline = deadline
        executable = shutil.which("git", path=os.defpath)
        if executable is None:
            raise vr.CicdV2Error("local_git_unavailable")
        self.argv = [
            executable,
            "--git-dir=" + str(self.metadata),
            "--work-tree=" + str(target),
            "-c", "core.hooksPath=/dev/null",
            "-c", "core.fsmonitor=false",
            "-c", "core.splitIndex=false",
            "-c", "core.logAllRefUpdates=true",
        ]
        self.env = {
            "PATH": os.defpath,
            "LC_ALL": "C",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
        }

    def run(
        self, *args: str, data: bytes | None = None,
        env: Mapping[str, str] | None = None, allowed: tuple[int, ...] = (0,),
    ) -> bytes:
        try:
            result = subprocess.run(
                [*self.argv, *args], cwd=self.target, env=self.env | dict(env or {}),
                input=data, stdin=subprocess.DEVNULL if data is None else None,
                capture_output=True, check=False, timeout=_remaining(self.deadline),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise vr.CicdV2Error("local_git_command_failed") from exc
        if result.returncode not in allowed:
            raise vr.CicdV2Error("local_git_command_failed")
        return result.stdout

    def oid(
        self, *args: str, data: bytes | None = None,
        env: Mapping[str, str] | None = None,
    ) -> str:
        output = self.run(*args, data=data, env=env).strip()
        if OID.fullmatch(output) is None:
            raise vr.CicdV2Error("invalid_local_git_oid")
        return output.decode("ascii")

    def location(self, relative: str) -> Path:
        path = self.metadata / relative
        for parent in path.parents:
            if parent == self.target:
                break
            if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
                raise vr.CicdV2Error("unsafe_git_repository")
        if path.is_symlink():
            raise vr.CicdV2Error("unsafe_git_repository")
        return path

    def reflogs(self) -> list[bytes]:
        return [
            _git_file(self.location("logs/" + name), missing=b"")
            for name in ("HEAD", self.branch)
        ]

    def attached(self) -> str:
        head = _git_file(self.location("HEAD"))
        if head != f"ref: {self.branch}\n".encode():
            raise vr.CicdV2Error("local_git_branch_changed")
        if self.run("symbolic-ref", "-q", self.branch, allowed=(0, 1)):
            raise vr.CicdV2Error("unsupported_local_git_repository")
        return self.oid("rev-parse", "--verify", self.branch)

    def repository(self) -> dict[str, Any]:
        if self.metadata.is_symlink() or not self.metadata.is_dir():
            raise vr.CicdV2Error("unsupported_local_git_repository")
        raw_config = _git_file(self.location("config"))
        version = re.match(rb"git version (\d+)\.(\d+)", self.run("--version"))
        if version is None or tuple(map(int, version.groups())) < (2, 30):
            raise vr.CicdV2Error("unsupported_local_git_version")
        self.run("check-ref-format", self.branch)
        self.location(self.branch)
        for name in (
            "commondir", "config.worktree", "objects/info/alternates",
            "objects/info/http-alternates", "MERGE_HEAD", "CHERRY_PICK_HEAD",
            "REVERT_HEAD", "rebase-merge", "rebase-apply", "sequencer",
        ):
            if self.location(name).exists():
                raise vr.CicdV2Error("unsupported_local_git_repository")
        config = self.run(
            "config", "--local", "--no-includes", "--name-only", "-z", "--list"
        )
        for key in config.split(b"\0"):
            lower = key.lower()
            if (
                lower.startswith((b"include.", b"includeif.", b"filter."))
                or lower.endswith(b".promisor")
                or lower in {
                    b"core.sparsecheckout", b"index.sparse", b"core.splitindex",
                }
                or (
                    lower.startswith(b"extensions.")
                    and lower not in {
                        b"extensions.objectformat", b"extensions.refstorage",
                    }
                )
            ):
                raise vr.CicdV2Error("unsupported_local_git_repository")
        # Git 2.30 uses the files backend with no refStorage config.
        storage = self.run(
            "config", "--local", "--get", "extensions.refstorage", allowed=(0, 1)
        )
        if storage.strip() not in (b"", b"files"):
            raise vr.CicdV2Error("unsupported_local_git_repository")
        # Check known Git write paths independently of Git's own path handling.
        objects = self.location("objects")
        if not objects.is_dir():
            raise vr.CicdV2Error("unsupported_local_git_repository")
        for entry in objects.iterdir():
            if re.fullmatch(r"[0-9a-f]{2}", entry.name):
                if entry.is_symlink() or not entry.is_dir():
                    raise vr.CicdV2Error("unsafe_git_repository")
        info = self.metadata.stat(follow_symlinks=False)
        return {
            "directory": [info.st_dev, info.st_ino],
            "config_sha256": _sha(raw_config),
        }


def _log_basis(data: list[bytes]) -> list[dict[str, Any]]:
    return [{"bytes": len(log), "sha256": _sha(log)} for log in data]


def _baseline(git: Git) -> dict[str, Any]:
    repository = git.repository()
    return {
        "repository": repository,
        "head": git.attached(),
        "index_file_sha256": _sha(_git_file(git.location("index"))),
        "reflogs": _log_basis(git.reflogs()),
    }


def baseline(target: Path, branch: str, deadline: float) -> dict[str, Any]:
    return _baseline(Git(target, branch, deadline))


def _identity(git: Git) -> tuple[str, str]:
    identity = []
    for key in ("user.name", "user.email"):
        value = git.run("config", "--local", "--no-includes", "--get", key).decode()
        text = value.removesuffix("\n")
        if not IDENT.fullmatch(text) or text.strip() != text:
            raise vr.CicdV2Error("invalid_local_git_identity")
        identity.append(text)
    return identity[0], identity[1]


def _message(input_id: str) -> str:
    return f"Millrace CI repair\n\nCI input: {input_id}\n"


def _commit_bytes(tree: str, parent: str, plan: Mapping[str, Any]) -> bytes:
    identity = _ident(plan)
    return (
        f"tree {tree}\nparent {parent}\nauthor {identity}\ncommitter {identity}\n\n"
        + _message(plan["input_id"])
    ).encode()


def _ident(plan: Mapping[str, Any]) -> str:
    return f'{plan["name"]} <{plan["email"]}> {plan["timestamp"]} +0000'


def _committer_env(plan: Mapping[str, Any]) -> dict[str, str]:
    return {
        "GIT_COMMITTER_NAME": plan["name"],
        "GIT_COMMITTER_EMAIL": plan["email"],
        "GIT_COMMITTER_DATE": f'@{plan["timestamp"]} +0000',
    }


def _stage(
    git: Git, home: Any, paths: list[str], delta: Mapping[str, Any],
    parent: str, directory: Path, *, isolated: bool,
) -> tuple[dict[str, str], bytes]:
    from .cicd_v2_runtime import _read

    path = directory / "index"
    path.write_bytes(_git_file(git.location("index")))
    env = {"GIT_INDEX_FILE": str(path)}
    if isolated:
        objects = directory / "objects"
        (objects / "info").mkdir(parents=True)
        alternate = os.fsencode(git.metadata / "objects")
        if b"\n" in alternate or b"\r" in alternate:
            raise vr.CicdV2Error("unsupported_local_git_repository")
        (objects / "info/alternates").write_bytes(alternate + b"\n")
        env["GIT_OBJECT_DIRECTORY"] = str(objects)
    removed, added = [], []
    for name in paths:
        file = delta["after"].get(name)
        encoded = name.encode("utf-8")
        if file is None:
            removed.append(b"0 " + b"0" * len(parent) + b"\t" + encoded + b"\0")
        else:
            payload = _read(home.resolve_ref(delta["payloads"][name]))
            if _sha(payload) != file["sha256"] or file["kind"] != "file":
                raise vr.CicdV2Error("local_git_payload_mismatch")
            mode = b"100755" if file["mode"] & 0o100 else b"100644"
            oid = git.oid(
                "hash-object", "-w", "--no-filters", "--stdin", data=payload, env=env
            )
            added.append(mode + b" " + oid.encode() + b"\t" + encoded + b"\0")
    git.run(
        "update-index", "-z", "--index-info", data=b"".join(removed + added), env=env
    )
    changed = git.run(
        "diff", "--cached", "--name-only", "-z", "--no-renames", "--no-ext-diff",
        parent, "--", env=env,
    ).split(b"\0")
    if sorted(x.decode("utf-8") for x in changed if x) != paths:
        raise vr.CicdV2Error("local_git_scope_mismatch")
    tree = git.oid("write-tree", env=env)
    index = _git_file(path)
    return {
        "tree": tree,
        "index_sha256": _sha(git.run("ls-files", "--stage", "-z", env=env)),
        "index_file_sha256": _sha(index),
    }, index


def freeze_plan(
    home: Any, input_id: str, target: Mapping[str, Any], patch: Mapping[str, Any],
    view: Mapping[str, Any], initial: Mapping[str, Any], deadline: float,
) -> dict[str, Any]:
    from .cicd_v2_runtime import _object, _read

    git = Git(home.target, target["branch"], deadline)
    if _baseline(git) != initial or initial["head"] != target["base_head"]:
        raise vr.CicdV2Error("delivery_git_changed")
    before = home.read_ref(
        _object(_read(home.workspace / (input_id + ".freeze.json")))
    )["snapshot"]
    pointer = home.workspace / (patch["attempt_id"] + ".observed-delta.json")
    delta_ref = _object(_read(pointer))
    delta = home.read_ref(delta_ref)
    paths = patch["changed_paths"]
    if (
        delta["before"] != before["source"] or delta["after"] != view["source"]
        or delta["changed_paths"] != paths or before["links"] != view["links"]
        or paths != sorted(
            name for name in set(delta["before"]) | set(delta["after"])
            if delta["before"].get(name) != delta["after"].get(name)
        )
    ):
        raise vr.CicdV2Error("local_git_scope_mismatch")
    name, email = _identity(git)
    submitted = home.task(input_id)["submitted_at"]
    plan = {
        "format": PLAN,
        "input_id": input_id,
        "config_sha256": home.config_sha256,
        "target": dict(target),
        "baseline": initial,
        "patch_ref": patch["patch_ref"],
        "delta_ref": delta_ref,
        "changed_paths": paths,
        "name": name,
        "email": email,
        "timestamp": int(datetime.fromisoformat(submitted).timestamp()),
    }
    identity = git.run("var", "GIT_COMMITTER_IDENT", env=_committer_env(plan))
    if identity != (_ident(plan) + "\n").encode():
        raise vr.CicdV2Error("invalid_local_git_identity")
    with tempfile.TemporaryDirectory(dir=home.workspace) as temp:
        result, _ = _stage(
            git, home, paths, delta, target["base_head"], Path(temp), isolated=True
        )
    plan.update(result)
    plan["commit"] = git.oid(
        "hash-object", "-t", "commit", "--stdin",
        data=_commit_bytes(plan["tree"], target["base_head"], plan),
    )
    if _baseline(git) != initial:
        raise vr.CicdV2Error("delivery_git_changed")
    return home.retain(plan)


def plan_for(home: Any, target: Mapping[str, Any]) -> dict[str, Any]:
    ref = target.get("commit_plan_ref")
    if ref is None:
        raise vr.CicdV2Error("local_git_plan_missing")
    code = "invalid_local_git_plan"
    plan = vr._record(
        home.read_ref(ref),
        ("format", "input_id", "config_sha256", "target", "baseline", "patch_ref",
         "delta_ref", "changed_paths", "name", "email", "timestamp", "tree",
         "index_sha256", "index_file_sha256", "commit"),
        PLAN, code,
    )
    vr._ref(plan["patch_ref"], code)
    vr._ref(plan["delta_ref"], code)
    expected = {key: value for key, value in target.items() if key != "commit_plan_ref"}
    if (
        plan["target"] != expected
        or plan["config_sha256"] != home.config_sha256
        or plan["patch_ref"]["sha256"] != target["patch_sha256"]
        or plan["baseline"]["head"] != target["base_head"]
    ):
        raise vr.CicdV2Error(code)
    for key in ("tree", "commit"):
        vr._revision(plan[key], code)
    for key in ("index_sha256", "index_file_sha256"):
        vr._sha(plan[key], code)
    vr._paths(plan["changed_paths"], code, nonempty=True)
    home.resolve_ref(plan["patch_ref"])
    home.resolve_ref(plan["delta_ref"])
    return plan


def _observe(git: Git, plan: Mapping[str, Any]) -> str:
    initial = plan["baseline"]
    if git.repository() != initial["repository"]:
        return "uncertain"
    if any(
        git.location(name + ".lock").exists()
        for name in ("HEAD", "index", git.branch)
    ):
        return "uncertain"
    head = git.attached()
    index = git.location("index")
    logs = git.reflogs()
    if head == initial["head"]:
        if (
            _sha(_git_file(index)) == initial["index_file_sha256"]
            and _log_basis(logs) == initial["reflogs"]
        ):
            return "not_applied"
        return "uncertain"
    if head != plan["commit"]:
        return "uncertain"
    # A normal Git stat refresh can rewrite the index without changing entries.
    # Require safe index metadata and compare the complete staged listing below.
    _git_file(index)
    if (
        git.run("cat-file", "commit", head)
        != _commit_bytes(plan["tree"], initial["head"], plan)
        or _sha(git.run("ls-files", "--stage", "-z")) != plan["index_sha256"]
        or git.run("diff", "--cached", "--name-only", "-z", "--no-ext-diff", head, "--")
    ):
        return "uncertain"
    line = f'{initial["head"]} {head} {_ident(plan)}\t{REFLOG_MESSAGE}\n'.encode()
    if any(
        _log_basis([raw[:basis["bytes"]]]) != [basis] or raw[basis["bytes"]:] != line
        for raw, basis in zip(logs, initial["reflogs"], strict=True)
    ):
        return "uncertain"
    return "applied"


def observe(home: Any, target: Mapping[str, Any], deadline: float) -> str:
    git = Git(home.target, target["branch"], deadline)
    return _observe(git, plan_for(home, target))


@contextmanager
def _index_lock(git: Git) -> Iterator[tuple[int, Path, Callable[[], None]]]:
    path = git.location("index.lock")
    mode = git.location("index").stat(follow_symlinks=False).st_mode
    fd = os.open(path, os.O_WRONLY | os.O_EXCL | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    info = os.fstat(fd)
    preserve = False

    def preserve_prepared_index() -> None:
        nonlocal preserve
        preserve = True

    try:
        os.fchmod(fd, stat.S_IMODE(mode))
        yield fd, path, preserve_prepared_index
    finally:
        os.close(fd)
        if not preserve:
            try:
                current = path.stat(follow_symlinks=False)
                if (current.st_dev, current.st_ino) == (info.st_dev, info.st_ino):
                    path.unlink()
            except FileNotFoundError:
                pass


def _ref_transaction(
    git: Git, plan: Mapping[str, Any], recheck: Callable[[], None],
    preserve_prepared_index: Callable[[], None],
) -> None:
    parent = plan["baseline"]["head"]
    env = git.env | _committer_env(plan)
    args = [*git.argv, "update-ref", "-m", REFLOG_MESSAGE, "--stdin"]
    with subprocess.Popen(
        args, cwd=git.target, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, bufsize=0,
    ) as process:
        assert process.stdin is not None and process.stdout is not None
        try:
            queue = f'start\nupdate HEAD {plan["commit"]} {parent}\nprepare\n'
            process.stdin.write(queue.encode())
            for expected in (b"start: ok\n", b"prepare: ok\n"):
                ready, _, _ = select.select(
                    [process.stdout], [], [], _remaining(git.deadline)
                )
                if not ready or process.stdout.readline() != expected:
                    raise vr.CicdV2Error("local_git_ref_conflict")
            # The files backend holds both HEAD and its referent after prepare.
            # Check the attached branch while Git holds those locks.
            try:
                recheck()
            except (OSError, vr.CicdV2Error):
                process.communicate(b"abort\n", timeout=_remaining(git.deadline))
                raise
            timeout = _remaining(git.deadline)
            # A timeout or lost acknowledgment can follow a successful update.
            preserve_prepared_index()
            output, _ = process.communicate(b"commit\n", timeout=timeout)
            if process.returncode or output != b"commit: ok\n":
                raise vr.CicdV2Error("local_git_ref_uncertain")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def _execute(home: Any, git: Git, plan: Mapping[str, Any]) -> str:
    expected_source = plan["target"]["source_after_sha256"]

    def recheck() -> None:
        if _baseline(git) != plan["baseline"]:
            raise vr.CicdV2Error("delivery_git_changed")
        if source_identity(home.observe()) != expected_source:
            raise vr.CicdV2Error("source_changed")

    with _index_lock(git) as (fd, lock, preserve_prepared_index):
        recheck()
        with tempfile.TemporaryDirectory(dir=home.workspace) as temp:
            facts, index = _stage(
                git, home, plan["changed_paths"], home.read_ref(plan["delta_ref"]),
                plan["baseline"]["head"], Path(temp), isolated=False,
            )
        if any(plan[key] != value for key, value in facts.items()):
            raise vr.CicdV2Error("local_git_tree_changed")
        commit = git.oid(
            "hash-object", "-t", "commit", "-w", "--stdin",
            data=_commit_bytes(plan["tree"], plan["baseline"]["head"], plan),
        )
        if commit != plan["commit"]:
            raise vr.CicdV2Error("local_git_commit_changed")
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(index)
            stream.flush()
            os.fsync(fd)
        _ref_transaction(git, plan, recheck, preserve_prepared_index)
        info, current = os.fstat(fd), lock.stat(follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
            raise vr.CicdV2Error("local_git_index_conflict")
        os.replace(lock, git.location("index"))
    return _observe(git, plan)


def main(argv: list[str] | None = None) -> int:
    from . import cicd_effects as effects
    from .cicd_v2_runtime import V2Home, _object, _read
    from .local_intake import IntakeError

    try:
        request_path, result_path = map(Path, sys.argv[1:] if argv is None else argv)
        home = V2Home(request_path.parents[2])
        request = effects.validate_request(_object(_read(request_path)))
        operation = request["operation"]
        with home.lock():
            state = home.load_state(request["input_id"])
            _, history = effects.history(home, state)
            last = history[-1]
            stem = request["effect_id"] + "-" + last["invocation"]["invocation_id"]
            if (
                not effects.is_local_git(home) or last["status"] != "reserved"
                or last["operation"] != operation
                or home.resolve_ref(last["request_ref"]) != request_path
                or result_path != home.workspace / (stem + ".effect-result.json")
                or result_path.exists()
                or last["invocation"]["pid"] != os.getppid()
            ):
                return 1
            plan = plan_for(home, request["target"])
            deadline = last["invocation"]["deadline"]
            git = Git(home.target, request["target"]["branch"], deadline)
            try:
                status = _observe(git, plan)
                if operation == "prepare":
                    status = "prepared" if status == "not_applied" else "refused"
                elif operation == "execute":
                    if status == "not_applied":
                        status = _execute(home, git, plan)
                    else:
                        status = "refused"
            except (OSError, ValueError, vr.CicdV2Error, subprocess.TimeoutExpired):
                status = "refused" if operation == "prepare" else "uncertain"
        observed_head = plan["commit"] if status == "applied" else None
        evidence = []
        if status in {"applied", "not_applied"}:
            proof = {
                "format": effects.EVIDENCE,
                "effect_id": request["effect_id"],
                "intent_sha256": vr.digest(request | {"operation": "execute"}),
                "target_sha256": vr.digest(request["target"]),
                "status": status,
                "remote_id": observed_head,
                "observed_head": observed_head,
            }
            raw = canonical(proof)
            path = result_path.with_suffix(".evidence.json")
            write_exact(path, raw)
            evidence = [cc.make_ref(path.name, raw)]
        result = {
            "format": effects.RESULT,
            "delivery_id": request["delivery_id"],
            "effect_id": request["effect_id"],
            "request_sha256": vr.digest(request),
            "operation": operation,
            "status": status,
            "remote_id": observed_head,
            "observed_head": observed_head,
            "artifact_sha256": None,
            "evidence_refs": evidence,
            "observed_at": datetime.now(UTC).isoformat(),
            "diagnostic": (
                None if status in {"prepared", "applied", "not_applied"}
                else "local_git_unproven"
            ),
        }
        write_exact(result_path, canonical(effects.validate_result(result, request)))
        return 0
    except (OSError, ValueError, KeyError, IndexError, vr.CicdV2Error, IntakeError):
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
