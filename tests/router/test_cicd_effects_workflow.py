"""Public CI-v2 journeys for local Git and custom commit effects."""

from __future__ import annotations

import copy
import hashlib
import json
import signal
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from cicd_support import DEFECT, GIT_ENV, event, git, new_id, write_json
from cicd_v2_support import initialized
from test_cicd_effects import approval_decision, configure_effects
from test_cicd_workflow import _resume, _run, _start, drive

from millrace_plus import cicd_cli, cicd_git_commit
from millrace_plus import cicd_effects as effects
from millrace_plus.cicd_v2_records import CicdV2Error
from millrace_plus.cicd_v2_runtime import Runtime, V2Home
from millrace_plus.router import control


def _checked(root, scenario="normal", *, configure=None):
    fx = initialized(root, configure=configure or configure_effects(root, scenario))
    home = V2Home(fx["home"])
    fx["git_before"] = git(home.target, "rev-parse", "HEAD")
    fx["index_before"] = git(home.target, "ls-files", "--stage", "-z")
    accepted = home.submit(event(root, fx["head"]))
    _start(fx)
    state = home.load_state(accepted["input_id"])
    assert home.read_ref(state["hold_ref"])["status"] == "checked_patch", _run(fx)
    assert not (root / "effect-ledger").exists()
    return fx, home, accepted["input_id"]


def _sigkill_local_git_peer(witness, phase):
    return textwrap.dedent(f"""\
        import hashlib
        import json
        import os
        import signal
        from millrace_plus import cicd_git_commit as peer

        witness = Path({str(witness)!r})
        phase = {phase!r}
        if not witness.exists():
            def kill(git):
                pid = os.getpid()
                group = os.getpgrp()
                assert pid == group == os.getsid(0)
                names = ["index.lock"]
                if phase == "prepared_refs":
                    names += ["HEAD.lock", git.branch + ".lock"]
                hashes = {{
                    name: hashlib.sha256(git.location(name).read_bytes()).hexdigest()
                    for name in names
                }}
                with witness.open("x") as evidence:
                    json.dump({{"pid": pid, "pgid": group, "phase": phase,
                               "lock_sha256": hashes}}, evidence)
                os.killpg(group, signal.SIGKILL)

            if phase == "index_lock_only":
                peer._baseline = kill
            else:
                transaction = peer._ref_transaction

                def kill_after_prepare(git, plan, recheck, preserve):
                    return transaction(git, plan, lambda: kill(git), preserve)

                peer._ref_transaction = kill_after_prepare
        """)


def _configure_local_git(root, *, fault=None):
    def configure(value):
        configure_effects(root)(value)
        adapter = value["external_policy"]["adapters"]["commit"]
        adapter.update(
            backend="local_git", prepare="local-git", execute="local-git",
            inspect="local-git",
        )
        target = Path(value["target_root"])
        git(target, "config", "--local", "user.name", "Fixture CI Operator")
        git(target, "config", "--local", "user.email", "fixture-ci@example.invalid")
        (target / "src/notes.txt").write_text("dirty operator notes\n")
        (target / "src/untracked.txt").write_text("untracked operator notes\n")
        (target / "millrace_plus.py").write_text(
            'raise RuntimeError("target import shadowed installed peer")\n'
        )
        commands = value["commands"]
        command = next(row for row in commands if row["id"] == "effect-prepare")
        command.update(
            id="local-git",
            argv=[
                sys.executable, "-I", "-m", "millrace_plus.cicd_git_commit",
                "{request_file}", "{result_file}",
            ],
        )
        value["commands"] = [
            row for row in commands if not row["id"].startswith("effect-")
        ]
        wrapper = root / "local_git_fault.py"
        if fault:
            before = after = ""
            if fault == "drop_send":
                marker = str(root / "dropped-first-send")
                before = (
                    f"marker = Path({marker!r})\n"
                    "if not marker.exists():\n"
                    "    marker.touch()\n"
                    "    raise SystemExit(0)\n"
                )
            elif fault == "lose_response":
                after = "if code == 0:\n    Path(sys.argv[2]).unlink()\n"
            elif fault == "split_index":
                before = (
                    "from millrace_plus import cicd_git_commit as peer\n"
                    "send = peer._ref_transaction\n"
                    "def fail_after_ref(*args, **kwargs):\n"
                    "    send(*args, **kwargs)\n"
                    "    raise OSError('interrupted after ref commit')\n"
                    "peer._ref_transaction = fail_after_ref\n"
                )
            elif fault in {"index_lock_only", "prepared_refs"}:
                before = _sigkill_local_git_peer(root / "kill-witness.json", fault)
            elif fault == "stage_after":
                after = (
                    "import subprocess\n"
                    "if code == 0:\n"
                    "    subprocess.run(['git', 'add', '--', 'src/notes.txt'], "
                    "check=True)\n"
                )
            wrapper.write_text(
                "import sys\nfrom pathlib import Path\n"
                "from millrace_plus.cicd_git_commit import main\n"
                + before + "code = main()\n" + after + "raise SystemExit(code)\n"
            )
            adapter["execute"] = "local-git-fault"
            value["commands"].append(
                command | {
                    "id": "local-git-fault",
                    "argv": [
                        sys.executable, "-I", str(wrapper),
                        "{request_file}", "{result_file}",
                    ],
                }
            )

    return configure


def _checked_local_git(root, *, fault=None):
    return _checked(root, configure=_configure_local_git(root, fault=fault))


def _approve(fx, home, input_id):
    decision = approval_decision(home, input_id)
    path = write_json(fx["root"] / "approve-delivery.json", decision)
    args = [
        "decide",
        "--workflow-home",
        str(fx["home"]),
        "--event",
        input_id,
        "--request-id",
        decision["request_id"],
        "--decision",
        "approve_delivery",
        "--evidence",
        str(path),
    ]
    assert cicd_cli.main(args) == 0
    return decision


def _ledger(fx):
    path = fx["root"] / "effect-ledger/operations.jsonl"
    return (
        []
        if not path.exists()
        else [json.loads(line) for line in path.read_text().splitlines()]
    )


def _unchanged_git(fx):
    assert git(fx["target"], "rev-parse", "HEAD") == fx["git_before"]
    assert git(fx["target"], "ls-files", "--stage", "-z") == fx["index_before"]
    assert (fx["target"] / "src/notes.txt").read_text() == "calculator notes\n"


def _backup_stale_git_locks(metadata, backup, stale):
    backup.mkdir(mode=0o700)
    for path, raw in stale.items():
        info = path.lstat()
        assert stat.S_ISREG(info.st_mode) and info.st_nlink == 1
        assert path.read_bytes() == raw
    for path, raw in stale.items():
        destination = backup / path.relative_to(metadata)
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.rename(destination)
        assert destination.read_bytes() == raw


def _cancel(fx, input_id):
    assert (
        cicd_cli.main(
            [
                "cancel",
                "--workflow-home",
                str(fx["home"]),
                "--event",
                input_id,
                "--request-id",
                new_id(),
            ]
        )
        == 0
    )


def _stop(fx):
    view = _run(fx)
    receipt = control.cancel_run(
        str(fx["instance"]),
        str(view["id"]),
        state_home=fx["state"],
        request_id=new_id(),
    )
    assert receipt["status"] != "refused", receipt
    stopped = control.inspect_run(
        str(fx["instance"]), str(view["id"]), state_home=fx["state"]
    )
    assert stopped["quiescent"]
    assert all(row["writer_state"] == "stopped" for row in stopped["invocations"])
    return stopped


def _next_no_action(fx, home):
    second = home.submit(
        event(
            fx["root"],
            fx["head"],
            name="after-effect",
            run_id="after-effect",
            conclusion="success",
        )
    )
    drive(fx, _resume)
    assert V2Home(fx["home"]).task(second["input_id"])["status"] == "succeeded"
    return second


def test_public_approved_fake_commit_completes_and_terminal_replay_advances_fifo(
    tmp_path,
):
    fx, home, input_id = _checked(tmp_path)
    decision = _approve(fx, home, input_id)
    view = drive(fx, _resume)
    reloaded = V2Home(fx["home"])
    task = reloaded.task(input_id)
    assert task["status"] == "succeeded", view
    receipt = task["terminal_receipt"]
    report = reloaded.read_ref(reloaded.load_state(input_id)["settlement_ref"])
    assert report["status"] == "published"
    assert report["delivery_refs"]
    assert reloaded.decide(decision) == home.decide(decision)
    assert reloaded.task(input_id)["terminal_receipt"] == receipt
    nodes = [row["node"] for row in view["invocations"]]
    assert (
        nodes.index("prepare_effect")
        < nodes.index("execute_effect")
        < nodes.index("settle")
    )
    assert [row["operation"] for row in _ledger(fx)] == ["prepare", "execute"]
    assert _ledger(fx)[-1]["saved_before_execute"] is True
    assert all(row["writer_state"] == "stopped" for row in view["invocations"])
    _next_no_action(fx, reloaded)
    assert V2Home(fx["home"]).task(input_id)["terminal_receipt"] == receipt
    _unchanged_git(fx)
    _stop(fx)


def _assert_identity_hold(fx, home, input_id, reason):
    assert home.task(input_id)["status"] == "held", _run(fx)
    state = home.load_state(input_id)
    report = home.read_ref(state["hold_ref"])
    assert report["status"] == "held_stale", report
    assert report["hold_reason"] == reason, report
    assert not state["attempt_refs"]
    assert not state["delivery_refs"]
    assert home.status(input_id)["inputs"][0]["delivery_basis_ref"] is None
    assert not (home.workspace / (input_id + ".freeze.json")).exists()
    assert (home.target / "src/calc.py").read_text() == DEFECT
    assert not {
        "reproduce", "investigate", "repair", "checks", "review_1",
        "prepare_effect", "execute_effect", "inspect_effect",
    }.intersection(row["node"] for row in _run(fx)["invocations"])


def test_public_local_git_global_identity_holds_until_new_attempt(
    tmp_path, monkeypatch,
):
    fx = initialized(tmp_path, configure=_configure_local_git(tmp_path))
    home = V2Home(fx["home"])
    git_home = tmp_path / "git-home"
    git_home.mkdir()
    global_config = git_home / ".gitconfig"
    email = "global-ci@example.invalid"
    global_config.write_text(f"[user]\n\temail = {email}\n")
    monkeypatch.setenv("HOME", str(git_home))
    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)
    git(home.target, "config", "--local", "--unset", "user.email")
    visible = subprocess.run(
        ["git", "-C", str(home.target), "config", "--get", "user.email"],
        env=GIT_ENV | {"HOME": str(git_home), "GIT_CONFIG_GLOBAL": str(global_config)},
        check=True, capture_output=True, text=True, timeout=30,
    )
    assert visible.stdout.strip() == email

    original = event(tmp_path, fx["head"])
    first = home.submit(original)
    _start(fx)
    _assert_identity_hold(fx, home, first["input_id"], "local_git_identity_missing")
    _cancel(fx, first["input_id"])
    drive(fx, _resume)
    assert home.task(first["input_id"])["status"] == "cancelled"
    terminal = home.task(first["input_id"])["terminal_receipt"]
    assert terminal is not None

    git(home.target, "config", "--local", "user.email", "fixture-ci@example.invalid")
    assert home.submit(original) == first | {
        "receipt": first["receipt"] | {"disposition": "replayed"},
    }
    second = home.submit(event(tmp_path, fx["head"], name="second", attempt=2))
    assert second["input_id"] != first["input_id"]
    _resume(fx)
    state = home.load_state(second["input_id"])
    assert home.read_ref(state["hold_ref"])["status"] == "checked_patch"
    _approve(fx, home, second["input_id"])
    drive(fx, _resume)
    assert home.task(second["input_id"])["status"] == "succeeded"
    state = home.load_state(second["input_id"])
    assert home.read_ref(state["settlement_ref"])["status"] == "published"
    assert home.task(first["input_id"])["terminal_receipt"] == terminal
    assert git(home.target, "rev-list", "--count", f'{fx["head"]}..HEAD').strip() == "1"
    _stop(fx)


@pytest.mark.parametrize(
    "name", ["Fixture CI Operator,", "", ","],
    ids=["normalized", "empty", "punctuation_only"],
)
def test_public_local_git_invalid_identity_holds_before_reproduction(tmp_path, name):
    fx = initialized(tmp_path, configure=_configure_local_git(tmp_path))
    home = V2Home(fx["home"])
    git(home.target, "config", "--local", "user.name", name)
    accepted = home.submit(event(tmp_path, fx["head"]))
    _start(fx)
    _assert_identity_hold(fx, home, accepted["input_id"], "invalid_local_git_identity")
    _stop(fx)


@pytest.mark.parametrize("conclusion", ["success", "cancelled", "skipped"])
def test_public_local_git_no_action_keeps_baseline_without_identity(
    tmp_path, conclusion,
):
    fx = initialized(tmp_path, configure=_configure_local_git(tmp_path))
    home = V2Home(fx["home"])
    git(home.target, "config", "--local", "--unset", "user.name")
    git(home.target, "config", "--local", "--unset", "user.email")
    accepted = home.submit(event(tmp_path, fx["head"], conclusion=conclusion))
    input_id = accepted["input_id"]
    drive(fx, _start)
    assert home.task(input_id)["status"] == "succeeded"
    state = home.load_state(input_id)
    assert home.read_ref(state["settlement_ref"])["status"] == "no_action"
    pointer = home.workspace / (input_id + ".freeze.json")
    frozen = home.read_ref(json.loads(pointer.read_bytes()))
    baseline = frozen["delivery_git"]["local_git"]
    assert baseline["head"] == fx["head"]
    assert baseline["repository"]["config_sha256"] == hashlib.sha256(
        (home.target / ".git/config").read_bytes()
    ).hexdigest()
    assert not state["attempt_refs"]
    assert not state["delivery_refs"]
    _stop(fx)


@pytest.mark.parametrize("lose_response", [False, True])
def test_public_local_git_commit_and_inspect_same_intent(tmp_path, lose_response):
    fault = "lose_response" if lose_response else None
    fx, home, input_id = _checked_local_git(tmp_path, fault=fault)
    target = home.target
    basis = home.read_ref(home.status(input_id)["inputs"][0]["delivery_basis_ref"])
    plan = home.read_ref(basis["target"]["commit_plan_ref"])
    assert plan["changed_paths"] == ["src/calc.py"]
    loose = target / ".git/objects" / plan["commit"][:2] / plan["commit"][2:]
    assert not loose.exists()
    assert git(target, "rev-parse", "HEAD").strip() == fx["head"]
    assert git(target, "ls-files", "--stage", "-z") == fx["index_before"]
    _approve(fx, home, input_id)
    if lose_response:
        _resume(fx)
        assert home.task(input_id)["status"] == "held", _run(fx)
        state = home.load_state(input_id)
        delivery, operations = effects.history(home, state)
        assert operations[-1]["operation"] == "execute"
        assert operations[-1]["result_ref"] is None
        assert effects.disposition(home, state) == "inspect"
        assert git(target, "rev-parse", "HEAD").strip() == plan["commit"]
        index_file = target / ".git/index"
        before_refresh = index_file.read_bytes()
        git(target, "status")
        assert index_file.read_bytes() != before_refresh
        _stop(fx)
        view = drive(fx, _start)
        assert "inspect_effect" in [row["node"] for row in view["invocations"]]
        assert "execute_effect" not in [row["node"] for row in view["invocations"]]
    else:
        view = drive(fx, _resume)
    reloaded = V2Home(fx["home"])
    assert reloaded.task(input_id)["status"] == "succeeded", view
    state = reloaded.load_state(input_id)
    assert reloaded.read_ref(state["settlement_ref"])["status"] == "published"
    final_delivery, operations = effects.history(reloaded, state)
    if lose_response:
        assert final_delivery["intent_ref"] == delivery["intent_ref"]
    assert [row["operation"] for row in operations if row["status"] == "reserved"] == (
        ["prepare", "execute", "inspect"] if lose_response else ["prepare", "execute"]
    )
    if not lose_response:
        assert {row["command_id"] for row in operations} == {"local-git"}
    assert reloaded.read_ref(operations[-1]["result_ref"])["observed_head"] == (
        plan["commit"]
    )
    assert git(target, "rev-parse", "HEAD", "HEAD^", "HEAD^{tree}").splitlines() == [
        plan["commit"], fx["head"], plan["tree"],
    ]
    branch = "refs/heads/" + basis["target"]["branch"]
    assert git(target, "symbolic-ref", "HEAD").strip() == branch
    changed = git(target, "diff", "--name-only", "HEAD^", "HEAD")
    assert changed.splitlines() == ["src/calc.py"]
    assert not git(target, "diff", "--cached", "--name-only")
    index = git(target, "ls-files", "--stage", "-z")
    assert [r for r in index.split("\0") if not r.endswith("\tsrc/calc.py")] == [
        r for r in fx["index_before"].split("\0") if not r.endswith("\tsrc/calc.py")
    ]
    assert (target / "src/notes.txt").read_text() == "dirty operator notes\n"
    assert (target / "src/untracked.txt").read_text() == "untracked operator notes\n"
    assert git(target, "rev-list", "--count", f'{fx["head"]}..HEAD').strip() == "1"
    _stop(fx)


def test_public_local_git_recovers_interrupted_index_install(tmp_path):
    fx, home, input_id = _checked_local_git(tmp_path, fault="split_index")
    target = home.target
    basis = home.read_ref(home.status(input_id)["inputs"][0]["delivery_basis_ref"])
    plan = home.read_ref(basis["target"]["commit_plan_ref"])
    branch = "refs/heads/" + basis["target"]["branch"]
    metadata = target / ".git"
    lock = metadata / "index.lock"
    assert not lock.exists()
    _approve(fx, home, input_id)
    _resume(fx)
    assert home.task(input_id)["status"] == "held", _run(fx)
    state = home.load_state(input_id)
    delivery, operations = effects.history(home, state)
    assert operations[-1]["operation"] == "execute"
    assert operations[-1]["result_ref"] is None
    paths = [metadata / name for name in ("HEAD", branch, "index", "index.lock")]
    logs = [metadata / "logs" / name for name in ("HEAD", branch)]
    metadata_before = {path: path.read_bytes() for path in [*paths, *logs]}

    _resume(fx)
    assert home.task(input_id)["status"] == "held", _run(fx)
    state = home.load_state(input_id)
    unresolved, operations = effects.history(home, state)
    assert unresolved["intent_ref"] == delivery["intent_ref"]
    assert operations[-1]["operation"] == "inspect"
    assert operations[-1]["result_ref"] is None
    assert effects.disposition(home, state) == "inspect"
    assert state["settlement_ref"] is None
    assert {path: path.read_bytes() for path in metadata_before} == metadata_before
    _stop(fx)

    # Follow the operator's documented read-only checks before restoring the index.
    assert git(target, "symbolic-ref", "HEAD").strip() == branch
    assert git(target, "rev-parse", "HEAD", "HEAD^", "HEAD^{tree}").splitlines() == [
        plan["commit"], fx["head"], plan["tree"],
    ]
    initial = plan["baseline"]
    assert hashlib.sha256(metadata_before[metadata / "index"]).hexdigest() == (
        initial["index_file_sha256"]
    )
    prepared_digest = hashlib.sha256(metadata_before[lock]).hexdigest()
    assert prepared_digest == plan["index_file_sha256"]
    line = (
        f'{fx["head"]} {plan["commit"]} {plan["name"]} <{plan["email"]}> '
        f'{plan["timestamp"]} +0000\t{cicd_git_commit.REFLOG_MESSAGE}\n'
    ).encode()
    for path, expected in zip(logs, initial["reflogs"], strict=True):
        log = metadata_before[path]
        assert hashlib.sha256(log[:expected["bytes"]]).hexdigest() == expected["sha256"]
        assert log[expected["bytes"]:] == line
    worktree = [target / name for name in (
        "src/calc.py", "src/notes.txt", "src/untracked.txt", "millrace_plus.py",
    )]
    original = {path: path.read_bytes() for path in worktree}
    _backup_stale_git_locks(metadata, tmp_path / "stale-lock-backup", {
        lock: metadata_before[lock],
    })
    git(target, "read-tree", plan["commit"])
    assert {path: path.read_bytes() for path in worktree} == original
    assert not git(target, "diff", "--cached", "--name-only")
    index = git(target, "ls-files", "--stage", "-z").encode()
    assert hashlib.sha256(index).hexdigest() == plan["index_sha256"]

    view = drive(fx, _start)
    assert home.task(input_id)["status"] == "succeeded", view
    state = home.load_state(input_id)
    assert home.read_ref(state["settlement_ref"])["status"] == "published"
    completed, operations = effects.history(home, state)
    assert completed["intent_ref"] == delivery["intent_ref"]
    assert [row["operation"] for row in operations if row["status"] == "reserved"] == [
        "prepare", "execute", "inspect", "inspect",
    ]
    result = home.read_ref(operations[-1]["result_ref"])
    assert result["observed_head"] == plan["commit"]
    assert git(target, "rev-list", "--count", f'{fx["head"]}..HEAD').strip() == "1"
    _stop(fx)


@pytest.mark.parametrize("phase", ["index_lock_only", "prepared_refs"])
def test_public_local_git_recovers_pre_ref_commit_sigkill(tmp_path, phase):
    fx, home, input_id = _checked_local_git(tmp_path, fault=phase)
    target = home.target
    basis = home.read_ref(home.status(input_id)["inputs"][0]["delivery_basis_ref"])
    plan = home.read_ref(basis["target"]["commit_plan_ref"])
    initial = plan["baseline"]
    metadata = target / ".git"
    branch = "refs/heads/" + basis["target"]["branch"]
    index = metadata / "index"
    logs = [metadata / "logs" / name for name in ("HEAD", branch)]
    paths = [metadata / "HEAD", metadata / branch, index, *logs]
    original = {path: path.read_bytes() for path in paths}
    lock_names = ["index.lock", "HEAD.lock", branch + ".lock"]
    assert not any((metadata / name).exists() for name in lock_names)

    _approve(fx, home, input_id)
    _resume(fx)
    assert home.task(input_id)["status"] == "held", _run(fx)
    state = home.load_state(input_id)
    delivery, operations = effects.history(home, state)
    stopped = operations[-1]
    assert stopped["operation"] == "execute" and stopped["result_ref"] is None
    process = home.read_ref(stopped["process_ref"])
    witness = json.loads((tmp_path / "kill-witness.json").read_text())
    assert witness["pid"] == witness["pgid"] == process["pid"]
    assert witness["phase"] == phase
    assert process["exit_code"] == -signal.SIGKILL
    assert process["process_facts"] == {
        "root_reaped": True, "stdout_eof": True, "stderr_eof": True,
        "group_state": "closed",
    }
    expected_names = lock_names[:1] if phase == "index_lock_only" else lock_names
    assert list(witness["lock_sha256"]) == expected_names
    stale = {metadata / name: (metadata / name).read_bytes() for name in expected_names}
    lock_hashes = {
        path.relative_to(metadata).as_posix(): hashlib.sha256(raw).hexdigest()
        for path, raw in stale.items()
    }
    assert lock_hashes == witness["lock_sha256"]
    if phase == "index_lock_only":
        assert stale[metadata / "index.lock"] == b""
        assert not any((metadata / name).exists() for name in lock_names[1:])
    else:
        prepared = hashlib.sha256(stale[metadata / "index.lock"]).hexdigest()
        assert prepared == plan["index_file_sha256"]

    _resume(fx)
    assert home.task(input_id)["status"] == "held", _run(fx)
    state = home.load_state(input_id)
    unresolved, operations = effects.history(home, state)
    assert unresolved["intent_ref"] == delivery["intent_ref"]
    assert operations[-1]["operation"] == "inspect"
    assert operations[-1]["result_ref"] is None
    assert effects.disposition(home, state) == "inspect"
    assert state["settlement_ref"] is None
    assert {path: path.read_bytes() for path in original} == original
    assert {path: path.read_bytes() for path in stale} == stale
    _stop(fx)

    assert git(target, "symbolic-ref", "HEAD").strip() == branch
    assert git(target, "rev-parse", "HEAD").strip() == initial["head"]
    original_index = hashlib.sha256(index.read_bytes()).hexdigest()
    assert original_index == initial["index_file_sha256"]
    for log, original_facts in zip(logs, initial["reflogs"], strict=True):
        assert log.stat().st_size == original_facts["bytes"]
        assert hashlib.sha256(log.read_bytes()).hexdigest() == original_facts["sha256"]
    info = metadata.stat()
    repository = initial["repository"]
    assert [info.st_dev, info.st_ino] == repository["directory"]
    config_digest = hashlib.sha256((metadata / "config").read_bytes()).hexdigest()
    assert config_digest == repository["config_sha256"]
    _backup_stale_git_locks(metadata, tmp_path / "stale-lock-backup", stale)

    view = drive(fx, _start)
    assert home.task(input_id)["status"] == "succeeded", view
    state = home.load_state(input_id)
    assert home.read_ref(state["settlement_ref"])["status"] == "published"
    completed, operations = effects.history(home, state)
    assert completed["intent_ref"] == delivery["intent_ref"]
    sends = [row for row in operations if row["status"] == "reserved"]
    assert [row["operation"] for row in sends] == [
        "prepare", "execute", "inspect", "inspect", "execute",
    ]
    assert sends[1]["request_ref"] == sends[4]["request_ref"] == delivery["intent_ref"]
    inspect = next(
        row for row in operations
        if row["operation"] == "inspect" and row["status"] == "accepted"
    )
    assert home.read_ref(inspect["result_ref"])["status"] == "not_applied"
    assert git(target, "rev-parse", "HEAD").strip() == plan["commit"]
    assert git(target, "rev-list", "--count", f'{fx["head"]}..HEAD').strip() == "1"
    assert not git(target, "diff", "--cached", "--name-only")
    _stop(fx)


def test_public_local_git_retries_only_after_exact_absence(tmp_path):
    fx, home, input_id = _checked_local_git(tmp_path, fault="drop_send")
    _approve(fx, home, input_id)
    _resume(fx)
    assert home.task(input_id)["status"] == "held"
    assert git(home.target, "rev-parse", "HEAD").strip() == fx["head"]
    view = drive(fx, _resume)
    assert home.task(input_id)["status"] == "succeeded", view
    delivery, operations = effects.history(home, home.load_state(input_id))
    sends = [row for row in operations if row["status"] == "reserved"]
    assert [row["operation"] for row in sends] == [
        "prepare", "execute", "inspect", "execute",
    ]
    assert sends[1]["request_ref"] == sends[3]["request_ref"] == delivery["intent_ref"]
    inspection = next(
        row for row in operations
        if row["status"] == "accepted" and row["operation"] == "inspect"
    )
    assert home.read_ref(inspection["result_ref"])["status"] == "not_applied"
    assert git(home.target, "rev-list", "--count", f'{fx["head"]}..HEAD').strip() == "1"
    _stop(fx)


def test_public_local_git_holds_applied_commit_when_post_index_changes(tmp_path):
    fx, home, input_id = _checked_local_git(tmp_path, fault="stage_after")
    _approve(fx, home, input_id)
    _resume(fx)
    assert home.task(input_id)["status"] == "held", _run(fx)
    state = home.load_state(input_id)
    assert state["settlement_ref"] is None
    _, operations = effects.history(home, state)
    result = home.read_ref(operations[-1]["result_ref"])
    assert result["status"] == "applied"
    assert git(home.target, "rev-parse", "HEAD").strip() == result["observed_head"]
    staged = git(home.target, "diff", "--cached", "--name-only")
    assert staged.strip() == "src/notes.txt"
    assert git(home.target, "rev-list", "--count", f'{fx["head"]}..HEAD').strip() == "1"
    _stop(fx)


@pytest.mark.parametrize("drift", ["head", "branch", "index", "config", "source"])
def test_public_local_git_refuses_drift_before_send(tmp_path, drift):
    fx, home, input_id = _checked_local_git(tmp_path)
    _approve(fx, home, input_id)
    target = home.target
    if drift == "head":
        git(target, "commit", "--allow-empty", "-m", "operator commit")
    elif drift == "branch":
        git(target, "branch", "another-branch")
        git(target, "symbolic-ref", "HEAD", "refs/heads/another-branch")
    elif drift == "index":
        git(target, "add", "--", "src/notes.txt")
    elif drift == "config":
        git(target, "config", "--local", "user.name", "Changed")
    else:
        (target / "src/calc.py").write_text("# modified after approval\n")
    head = git(target, "rev-parse", "HEAD")
    index = (target / ".git/index").read_bytes()
    _resume(fx)
    view = _run(fx)
    assert home.task(input_id)["status"] == "held", view
    assert not any(row["node"] == "execute_effect" for row in view["invocations"])
    assert git(target, "rev-parse", "HEAD") == head
    assert (target / ".git/index").read_bytes() == index
    _stop(fx)


def test_public_local_git_refuses_mismatched_plan_permission(tmp_path):
    fx, home, input_id = _checked_local_git(tmp_path)
    decision = approval_decision(home, input_id)
    permission = home.read_ref(decision["permission_ref"])
    for changed in (False, True):
        mutated = copy.deepcopy(permission)
        target = mutated["effects"][0]["target"]
        if changed:
            target["commit_plan_ref"]["sha256"] = "f" * 64
        else:
            del target["commit_plan_ref"]
        with pytest.raises(CicdV2Error, match="^action_permission_mismatch$"):
            home.decide(decision | {
                "request_id": new_id(), "permission_ref": home.retain(mutated),
            })
    _resume(fx)
    view = _run(fx)
    assert home.task(input_id)["status"] == "held", view
    assert not any(row["node"].endswith("_effect") for row in view["invocations"])
    assert not home.load_state(input_id)["delivery_refs"]
    assert git(home.target, "rev-parse", "HEAD").strip() == fx["head"]
    _stop(fx)


@pytest.mark.parametrize("authorized_empty", [False, True])
def test_default_and_empty_effects_hold_without_peer_dispatch(
    tmp_path, authorized_empty
):
    def configure(value):
        configure_effects(tmp_path)(value)
        value["external_policy"]["effects"] = []
        value["external_policy"]["adapters"] = {}
        if not authorized_empty:
            value["external_policy"] = effects.vr.hold_external_policy()

    fx = initialized(tmp_path, configure=configure)
    home = V2Home(fx["home"])
    accepted = home.submit(event(tmp_path, fx["head"]))
    _start(fx)
    state = home.load_state(accepted["input_id"])
    assert home.read_ref(state["hold_ref"])["status"] == "checked_patch"
    assert home.status(accepted["input_id"])["inputs"][0]["delivery_basis_ref"] is None
    assert not _ledger(fx)
    assert not any(row["node"].endswith("_effect") for row in _run(fx)["invocations"])
    _stop(fx)


@pytest.mark.parametrize("scenario", ["lost_applied", "malformed_applied"])
def test_public_lost_applied_response_restarts_and_inspects_without_resend(
    tmp_path, scenario
):
    fx, home, input_id = _checked(tmp_path, scenario)
    _approve(fx, home, input_id)
    _resume(fx)
    assert home.task(input_id)["status"] == "held", _run(fx)
    state = home.load_state(input_id)
    delivery, operations = effects.history(home, state)
    intent = home.read_ref(delivery["intent_ref"])
    assert operations[-1]["operation"] == "execute"
    assert operations[-1]["process_ref"] is not None
    process = home.read_ref(operations[-1]["process_ref"])
    assert process["process_facts"]["group_state"] == "closed"
    assert process["process_facts"]["root_reaped"] is True
    assert effects.disposition(home, state) == "inspect"
    assert [row["operation"] for row in _ledger(fx)] == ["prepare", "execute"]
    old = _stop(fx)
    # A new public run starts at reconcile, reading the original run's exact
    # completion and the durable possible-send intent. No direct state edits.
    view = drive(fx, _start)
    restarted = V2Home(fx["home"])
    assert restarted.task(input_id)["status"] == "succeeded", view
    assert old["id"] != view["id"]
    operations = _ledger(fx)
    assert [row["operation"] for row in operations] == ["prepare", "execute", "inspect"]
    assert {row["effect_id"] for row in operations} == {intent["effect_id"]}
    assert len({row["intent_sha256"] for row in operations}) == 1
    assert "execute_effect" not in [row["node"] for row in view["invocations"]]
    assert "inspect_effect" in [row["node"] for row in view["invocations"]]
    _unchanged_git(fx)
    _stop(fx)


def test_public_identical_retry_only_after_exact_not_applied_inspection(tmp_path):
    fx, home, input_id = _checked(tmp_path, "not_applied_once")
    _approve(fx, home, input_id)
    _resume(fx)
    assert home.task(input_id)["status"] == "held"
    assert [row["operation"] for row in _ledger(fx)] == ["prepare", "execute"]
    drive(fx, _resume)
    assert V2Home(fx["home"]).task(input_id)["status"] == "succeeded"
    observations = _ledger(fx)
    assert [row["operation"] for row in observations] == [
        "prepare",
        "execute",
        "inspect",
        "execute",
    ]
    assert len({row["effect_id"] for row in observations}) == 1
    assert len({row["intent_sha256"] for row in observations}) == 1
    _unchanged_git(fx)
    _stop(fx)


@pytest.mark.parametrize("possible_send", [False, True])
def test_public_cancel_preserves_effect_aftermath_and_next_input(
    tmp_path, possible_send
):
    fx, home, input_id = _checked(tmp_path, "lost_applied")
    _approve(fx, home, input_id)
    if possible_send:
        _resume(fx)
        assert home.task(input_id)["status"] == "held"
    _cancel(fx, input_id)
    drive(fx, _resume)
    reloaded = V2Home(fx["home"])
    task = reloaded.task(input_id)
    assert task["status"] == "cancelled", _run(fx)
    receipt = task["terminal_receipt"]
    report = reloaded.read_ref(reloaded.load_state(input_id)["settlement_ref"])
    assert report["status"] == "cancelled"
    expected = ["prepare", "execute", "inspect"] if possible_send else []
    assert [row["operation"] for row in _ledger(fx)] == expected
    if possible_send:
        _, operations = effects.history(reloaded, reloaded.load_state(input_id))
        assert reloaded.read_ref(operations[-1]["result_ref"])["status"] == "applied"
    _next_no_action(fx, reloaded)
    assert V2Home(fx["home"]).task(input_id)["terminal_receipt"] == receipt
    _unchanged_git(fx)
    _stop(fx)


def test_pending_cancel_stays_held_and_cannot_resend(tmp_path):
    fx, home, input_id = _checked(tmp_path, "pending")
    _approve(fx, home, input_id)
    _resume(fx)
    _cancel(fx, input_id)
    _resume(fx)
    assert V2Home(fx["home"]).task(input_id)["status"] == "held"
    assert [row["operation"] for row in _ledger(fx)] == [
        "prepare",
        "execute",
        "inspect",
    ]
    assert effects.disposition(home, home.load_state(input_id)) == "inspect"
    _unchanged_git(fx)
    _stop(fx)


def test_authority_mutations_refuse_before_any_effect_peer(tmp_path):
    fx, home, input_id = _checked(tmp_path)
    decision = approval_decision(home, input_id)
    permission = home.read_ref(decision["permission_ref"])
    changes = [
        lambda p: p.update(input_id="foreign"),
        lambda p: p.update(candidate_id="foreign"),
        lambda p: p.update(config_sha256="f" * 64),
        lambda p: p.update(source_after_sha256="f" * 64),
        lambda p: p.update(patch_sha256="f" * 64),
        lambda p: p.update(authority_ref=home.retain({"foreign": "authority"})),
        lambda p: p["effects"][0]["target"].update(branch="foreign"),
        lambda p: p.update(
            approval_ref=home.retain(
                home.read_ref(p["approval_ref"]) | {"issuer": "foreign"}
            )
        ),
    ]
    for change in changes:
        mutated = copy.deepcopy(permission)
        change(mutated)
        changed = decision | {
            "request_id": new_id(),
            "permission_ref": home.retain(mutated),
        }
        with pytest.raises(CicdV2Error):
            home.decide(changed)
    assert not _ledger(fx)
    assert home.task(input_id)["status"] == "held"
    assert not home.load_state(input_id)["delivery_refs"]
    _stop(fx)


@pytest.mark.parametrize("advance", ["held_report", "state_version"])
def test_accepted_approval_rechecks_consuming_report_and_state(tmp_path, advance):
    fx, home, input_id = _checked(tmp_path)
    decision = _approve(fx, home, input_id)
    runtime = Runtime(home, fx["home"] / "workflow")
    state = home.load_state(input_id)
    # Force the old admission-to-consumption interleaving through the actual
    # domain CAS/report writers. No private Core or intake store edit occurs.
    with home.lock(), home.state_lock():
        if advance == "held_report":
            runtime._hold(state, "later_guard_report", "checked_patch")
        else:
            runtime._save(state)
    changed = home.load_state(input_id)
    assert changed["version"] == decision["expected_state_version"] + 1
    if advance == "held_report":
        assert changed["hold_ref"]["sha256"] != decision["basis_sha256"]
    else:
        assert changed["hold_ref"]["sha256"] == decision["basis_sha256"]
    _resume(fx)
    assert home.task(input_id)["status"] == "held"
    assert not home.load_state(input_id)["delivery_refs"]
    assert not _ledger(fx)
    assert not any(
        row["node"].endswith("_effect") for row in _run(fx)["invocations"]
    )
    _stop(fx)


def test_factory_source_mode_refuses_effects_before_preparation(tmp_path):
    def configure(value):
        configure_effects(tmp_path)(value)
        value["source_policy"]["mode"] = "factory"

    with pytest.raises(CicdV2Error, match="unsupported_until_full_ci_d1"):
        initialized(tmp_path, configure=configure)
    assert not (tmp_path / "home").exists()
    assert not (tmp_path / "effect-ledger").exists()


def test_source_drift_allows_inspection_but_holds_publication(tmp_path):
    fx, home, input_id = _checked(tmp_path, "lost_applied")
    _approve(fx, home, input_id)
    _resume(fx)
    (fx["target"] / "src/notes.txt").write_text("foreign later edit\n")
    _resume(fx)
    assert home.task(input_id)["status"] == "held"
    assert [row["operation"] for row in _ledger(fx)] == [
        "prepare",
        "execute",
        "inspect",
    ]
    _, operations = effects.history(home, home.load_state(input_id))
    assert home.read_ref(operations[-1]["result_ref"])["status"] == "applied"
    assert (fx["target"] / "src/notes.txt").read_text() == "foreign later edit\n"
    _stop(fx)


def _accepted_applied_inspection(tmp_path):
    fx, home, input_id = _checked(tmp_path, "lost_applied")
    _approve(fx, home, input_id)
    _resume(fx)
    notes = fx["target"] / "src/notes.txt"
    original_notes = notes.read_bytes()
    notes.write_text("owned drift keeps the accepted inspection held\n")
    _resume(fx)
    assert home.task(input_id)["status"] == "held"
    _, operations = effects.history(home, home.load_state(input_id))
    assert operations[-1]["operation"] == "inspect"
    assert operations[-1]["status"] == "observed"
    # Source/check validation holds before the delivery guard accepts the
    # inspection. Public reconcile accepts its stopped completion first.
    _resume(fx)
    assert home.task(input_id)["status"] == "held"
    _, operations = effects.history(home, home.load_state(input_id))
    observed = operations[-1]
    assert observed["operation"] == "inspect" and observed["status"] == "accepted"
    assert observed["format"] == effects.RETAINED_OPERATION
    result = home.read_ref(observed["result_ref"])
    assert result["status"] == "applied"
    assert [row["original_ref"] for row in observed["retained_evidence_refs"]] == (
        result["evidence_refs"]
    )
    intent = home.read_ref(observed["request_ref"])
    adapter_result = home.workspace / (
        intent["effect_id"]
        + "-"
        + observed["invocation"]["invocation_id"]
        + ".effect-result.json"
    )
    assert home.resolve_ref(observed["result_ref"]).read_bytes() == (
        adapter_result.read_bytes()
    )
    assert [row["operation"] for row in _ledger(fx)] == [
        "prepare",
        "execute",
        "inspect",
    ]
    return fx, home, input_id, observed, original_notes


@pytest.mark.parametrize(
    ("damage", "terminal"), [("missing", "succeeded"), ("changed", "cancelled")]
)
def test_accepted_proof_survives_original_loss_and_public_settlement(
    tmp_path, damage, terminal
):
    fx, home, input_id, observed, original_notes = _accepted_applied_inspection(
        tmp_path
    )
    result = home.read_ref(observed["result_ref"])
    mapping = observed["retained_evidence_refs"][0]
    original = home.resolve_ref(mapping["original_ref"])
    retained = home.resolve_ref(mapping["retained_ref"])
    assert original != retained
    proof_bytes = retained.read_bytes()
    if damage == "missing":
        original.unlink()
    else:
        original.write_bytes(b"adapter replaced its proof after observation\n")
    reloaded = V2Home(fx["home"])
    state = reloaded.load_state(input_id)
    _, inspected = effects.history(reloaded, state)
    assert inspected[-1] == observed
    assert reloaded.read_ref(inspected[-1]["result_ref"]) == result
    assert reloaded.status(input_id)["inputs"][0]["state"]["delivery_refs"] == (
        state["delivery_refs"]
    )
    (fx["target"] / "src/notes.txt").write_bytes(original_notes)
    if terminal == "cancelled":
        _cancel(fx, input_id)
    view = drive(fx, _resume)
    settled = V2Home(fx["home"])
    task = settled.task(input_id)
    assert task["status"] == terminal, view
    report = settled.read_ref(settled.load_state(input_id)["settlement_ref"])
    assert report["status"] == ("cancelled" if terminal == "cancelled" else "published")
    assert report["delivery_refs"]
    assert settled.task(input_id)["terminal_receipt"] == task["terminal_receipt"]
    assert [row["operation"] for row in _ledger(fx)] == [
        "prepare",
        "execute",
        "inspect",
    ]
    assert retained.read_bytes() == proof_bytes
    if damage == "missing":
        assert not original.exists()
    else:
        assert original.read_bytes() != proof_bytes
    _unchanged_git(fx)
    _stop(fx)


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_public_required_retained_proof_damage_refuses_with_valid_original(
    tmp_path, damage
):
    fx, home, input_id, observed, _ = _accepted_applied_inspection(tmp_path)
    mapping = observed["retained_evidence_refs"][0]
    original = home.resolve_ref(mapping["original_ref"])
    proof_bytes = original.read_bytes()
    retained = home.resolve_ref(mapping["retained_ref"])
    if damage == "missing":
        retained.unlink()
    else:
        retained.write_bytes(b"corrupt required retained proof\n")
    reloaded = V2Home(fx["home"])
    with pytest.raises((OSError, CicdV2Error)):
        effects.history(reloaded, reloaded.load_state(input_id))
    _resume(fx)
    assert _run(fx)["planes"]["main"]["status"] == "paused"
    assert original.read_bytes() == proof_bytes
    assert [row["operation"] for row in _ledger(fx)] == [
        "prepare",
        "execute",
        "inspect",
    ]
    if damage == "missing":
        assert not retained.exists()
    else:
        assert retained.read_bytes() == b"corrupt required retained proof\n"
    _stop(fx)


def test_direct_execute_reentry_cannot_dispatch_a_possible_send(tmp_path, monkeypatch):
    fx, home, input_id = _checked(tmp_path, "lost_applied")
    _approve(fx, home, input_id)
    _resume(fx)
    runtime = Runtime(home, fx["home"] / "workflow")
    state = home.load_state(input_id)
    _, operations = effects.history(home, state)
    context = operations[-1]["invocation"]
    # Real possible-send/public completion evidence, with only the caller's
    # current-invocation gate bypassed to target the duplicate-send refusal.
    monkeypatch.setattr(runtime, "_stopped", lambda *args: None)
    with pytest.raises(CicdV2Error, match="effect_inspection_required"):
        effects.operation(runtime, "execute", context)
    assert sum(row["operation"] == "execute" for row in _ledger(fx)) == 1
    _stop(fx)
