"""CI v2 trusted command execution through Core's public owned-process helper.

`run_command` cases launch real child processes in an owned temporary target;
the status matrix uses explicit fact objects for the shapes a real child cannot
produce on demand (forced may-live group, truncated stream).
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from millrace_plus import check_contracts as cc
from millrace_plus.cicd_records import CicdError
from millrace_plus.cicd_v2_checks import (
    environment_for,
    matched_failures,
    product_changes,
    run_command,
    status_of,
)
from millrace_plus.local_intake import canonical

GUARDS = {"command.stdout": 65536, "command.stderr": 65536}
REASON = {"reason": "test", "origin": "test fixture", "override": "edit fixture"}


def _policy() -> tuple[cc.PathPolicy, dict[str, object]]:
    raw = cc.encode(
        {
            "format": cc.PATH_POLICY,
            "rules": [
                {"surface": "environment_paths", "prefixes": list(cc.CACHE_TOKENS)}
                | REASON,
                {"surface": "product_paths", "prefixes": ["build"]} | REASON,
            ],
        }
    )
    return cc.validate_path_policy(cc.parse(raw)), cc.make_ref("path-policy.json", raw)


def _command(
    ref: dict[str, object], script: str, **changes: object
) -> dict[str, object]:
    command: dict[str, object] = {
        "id": "check",
        "argv": [sys.executable, "-c", script],
        "cwd": ".",
        # No bytecode: a same-size source fix within one mtime second would
        # otherwise run the first invocation's stale `__pycache__` entry.
        "environment": {
            "inherit_names": ["PATH"],
            "set": {"LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"},
        },
        "timeout_seconds": 60,
        "timeout_policy": REASON,
        "expected_exit_code": 0,
        "path_policy_ref": ref,
        "expected_products": [],
    }
    return command | changes


def _target(tmp_path: Path) -> Path:
    target = tmp_path / "target"
    (target / "src").mkdir(parents=True)
    (target / "src" / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    return target


def _run(
    target: Path, policy: cc.PathPolicy, command: dict[str, object], seconds: float = 60
) -> dict[str, object]:
    started: list[int] = []
    observed = run_command(
        target,
        policy,
        command,
        GUARDS,
        base_environment={"PATH": "/usr/bin:/bin", "SECRET_TOKEN": "s3cr3t"},
        deadline=time.time() + seconds,
        cancelled=lambda: False,
        on_started=started.append,
    )
    assert (observed["pid"] is None) == (not started)
    return observed


def test_environment_provenance_retains_digests_never_inherited_values() -> None:
    command = {"environment": {"inherit_names": ["PATH", "ABSENT"], "set": {"A": "1"}}}
    values, provenance = environment_for(command, {"PATH": "/bin", "OTHER": "x"})
    assert values == {"PATH": "/bin", "A": "1"}
    assert provenance == {
        "inherited_names": ["PATH"],
        "inherited_value_sha256": hashlib.sha256(
            canonical({"PATH": "/bin"})
        ).hexdigest(),
        "set_value_sha256": hashlib.sha256(canonical({"A": "1"})).hexdigest(),
        "path_observation_refs": [],
    }
    assert "/bin" not in json.dumps(provenance)


def test_literal_failure_signatures_match_only_their_named_stream() -> None:
    patterns = {
        "format": "millrace-plus-cicd-failure-patterns/v1",
        "patterns": [
            {"id": "assert", "stream": "stderr", "literal": "AssertionError: 2 != 4"},
            {"id": "banner", "stream": "stdout", "literal": "FAILED"},
        ],
    }
    out, err = b"1 FAILED\n", b"Traceback\nAssertionError: 2 != 4\n"
    assert matched_failures(out, err, ["assert", "banner"], patterns) == [
        "assert",
        "banner",
    ]
    # The same literal in the other stream is not a match: no regex, no search.
    assert matched_failures(err, out, ["assert", "banner"], patterns) == []


def test_reproduction_command_observes_real_defect_and_closed_group(
    tmp_path: Path,
) -> None:
    target = _target(tmp_path)
    policy, ref = _policy()
    script = (
        "import runpy, sys; calc = runpy.run_path('src/calc.py'); "
        "r = calc['add'](2, 2); "
        "sys.stderr.write(f'AssertionError: {r} != 4\\n') if r != 4 else None; "
        "sys.exit(1 if r != 4 else 0)"
    )
    observed = _run(target, policy, _command(ref, script))
    assert (observed["status"], observed["exit_code"]) == ("failed", 1)
    assert b"AssertionError: 0 != 4" in observed["stderr"]
    assert observed["process_facts"] == {
        "root_reaped": True,
        "stdout_eof": True,
        "stderr_eof": True,
        "group_state": "closed",
    }
    assert observed["source_before_sha256"] == observed["source_after_sha256"]
    # Inherited secret values never reach the observation.
    assert "s3cr3t" not in repr(
        {k: v for k, v in observed.items() if k not in {"stdout", "stderr"}}
    )
    (target / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    fixed = _run(target, policy, _command(ref, script))
    assert (fixed["status"], fixed["exit_code"], fixed["diagnostic"]) == (
        "passed",
        0,
        None,
    )


def test_declared_product_is_allowed_and_undeclared_output_is_uncertain(
    tmp_path: Path,
) -> None:
    target = _target(tmp_path)
    policy, ref = _policy()
    write = (
        "import pathlib; pathlib.Path('build').mkdir(); "
        "pathlib.Path('build/cov.xml').write_text('x')"
    )
    declared = _run(target, policy, _command(ref, write, expected_products=["build"]))
    assert declared["status"] == "passed"
    assert declared["products"]["declared"] == ["build", "build/cov.xml"]
    assert declared["products"]["undeclared"] == []
    (target / "build" / "cov.xml").unlink()
    (target / "build").rmdir()
    undeclared = _run(target, policy, _command(ref, write))
    assert (undeclared["status"], undeclared["diagnostic"]) == (
        "uncertain",
        "undeclared_output",
    )
    assert undeclared["products"]["undeclared"] == ["build", "build/cov.xml"]


def test_source_write_by_check_is_uncertain_not_pass(tmp_path: Path) -> None:
    target = _target(tmp_path)
    policy, ref = _policy()
    edit = "open('src/calc.py', 'a').write('# touched\\n')"
    observed = _run(target, policy, _command(ref, edit))
    assert (observed["status"], observed["diagnostic"]) == (
        "uncertain",
        "source_changed",
    )
    assert observed["source_before_sha256"] != observed["source_after_sha256"]


def test_timeout_and_missing_executable_never_pass(tmp_path: Path) -> None:
    target = _target(tmp_path)
    policy, ref = _policy()
    slow = _run(target, policy, _command(ref, "import time; time.sleep(30)"), seconds=1)
    assert slow["status"] == "timeout"
    missing = _run(
        target, policy, _command(ref, "x", argv=["/nonexistent/tool", "--check"])
    )
    assert (missing["status"], missing["diagnostic"], missing["pid"]) == (
        "infrastructure",
        "executable_unavailable",
        None,
    )


def test_output_capture_guards_are_required(tmp_path: Path) -> None:
    target = _target(tmp_path)
    policy, ref = _policy()
    with pytest.raises(CicdError) as refused:
        run_command(
            target,
            policy,
            _command(ref, "pass"),
            {},
            base_environment={},
            deadline=time.time() + 60,
            cancelled=lambda: False,
            on_started=lambda _pid: None,
        )
    assert refused.value.code == "resource_guard_required"


def _facts(**changes: object) -> SimpleNamespace:
    base = {
        "stop_cause": None,
        "exit_code": 0,
        "root_reaped": True,
        "stdout_eof": True,
        "stderr_eof": True,
        "group_state": "closed",
        "cleanup_confirmed": True,
        "stdout_truncated": False,
        "stderr_truncated": False,
    }
    return SimpleNamespace(**(base | changes))


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({}, ("passed", None)),
        ({"exit_code": 3}, ("failed", None)),
        (
            {"group_state": "may_live", "cleanup_confirmed": False},
            ("uncertain", "writer_not_closed"),
        ),
        ({"root_reaped": False}, ("uncertain", "writer_not_closed")),
        ({"stderr_eof": False}, ("uncertain", "writer_not_closed")),
        ({"stdout_truncated": True}, ("uncertain", "output_guard_exceeded")),
        ({"stop_cause": "cancelled", "exit_code": -15}, ("uncertain", "cancelled")),
        ({"stop_cause": "timeout", "exit_code": -15}, ("timeout", "timeout")),
        (
            {"stop_cause": "start_failed", "exit_code": None},
            ("infrastructure", "start_failed"),
        ),
    ],
)
def test_raw_status_requires_complete_closed_evidence(
    changes: dict[str, object], expected: tuple[str, str | None]
) -> None:
    assert (
        status_of(_facts(**changes), 0, source_unchanged=True, undeclared_output=False)
        == expected
    )


def test_product_changes_report_preexisting_declared_paths() -> None:
    policy, ref = _policy()
    command = _command(ref, "pass", expected_products=["build"])
    before = {"products": {"build": {"kind": "directory"}, "build/a": {"sha256": "1"}}}
    after = {
        "products": {
            "build": {"kind": "directory"},
            "build/a": {"sha256": "2"},
            "build/b": {"sha256": "3"},
        }
    }
    assert product_changes(policy, command, before, after) == {
        "declared": ["build/a", "build/b"],
        "undeclared": [],
        "preexisting": ["build/a"],
    }


def _products(target: Path, policy: cc.PathPolicy) -> dict[str, dict[str, object]]:
    from millrace_plus.cicd_source import snapshot_policy

    return snapshot_policy(target, policy)["products"]


def test_layer_owns_proven_absent_products_and_reverses_them_exactly(
    tmp_path: Path,
) -> None:
    from millrace_plus.cicd_v2_checks import product_ownership, reverse_products

    target = _target(tmp_path)
    policy, ref = _policy()
    write = (
        "import pathlib; pathlib.Path('build/sub').mkdir(parents=True); "
        "pathlib.Path('build/sub/cov.xml').write_text('x')"
    )
    observed = _run(target, policy, _command(ref, write, expected_products=["build"]))
    ownership = product_ownership(observed)
    assert ownership == {
        "owned_paths": ["build", "build/sub", "build/sub/cov.xml"],
        "foreign": [],
        "undeclared": [],
        "hold": None,
    }
    source = (target / "src" / "calc.py").read_bytes()
    after = observed["after"]["products"]
    reversed_ = reverse_products(
        target,
        ownership["owned_paths"],
        after,
        observed["before"]["products"],
        _products(target, policy),
    )
    assert reversed_["status"] == "reversed"
    assert not (target / "build").exists()
    assert (target / "src" / "calc.py").read_bytes() == source


def test_product_drift_or_foreign_entry_holds_with_nothing_removed(
    tmp_path: Path,
) -> None:
    from millrace_plus.cicd_v2_checks import product_ownership, reverse_products

    target = _target(tmp_path)
    policy, ref = _policy()
    write = (
        "import pathlib; pathlib.Path('build').mkdir(); "
        "pathlib.Path('build/cov.xml').write_text('x')"
    )
    observed = _run(target, policy, _command(ref, write, expected_products=["build"]))
    owned = product_ownership(observed)["owned_paths"]
    after, before = observed["after"]["products"], observed["before"]["products"]
    (target / "build" / "cov.xml").write_text("changed by someone else")
    drift = reverse_products(target, owned, after, before, _products(target, policy))
    assert (drift["status"], drift["reason"], drift["removed"]) == (
        "held",
        "product_drift",
        [],
    )
    assert (target / "build" / "cov.xml").read_text() == "changed by someone else"
    (target / "build" / "cov.xml").write_text("x")
    (target / "build" / "foreign.txt").write_text("not this layer's")
    foreign = reverse_products(target, owned, after, before, _products(target, policy))
    assert (foreign["status"], foreign["reason"], foreign["removed"]) == (
        "held",
        "directory_not_empty",
        [],
    )
    assert (target / "build" / "cov.xml").exists()
    assert (target / "build" / "foreign.txt").exists()


def test_preexisting_foreign_product_is_never_adopted(tmp_path: Path) -> None:
    from millrace_plus.cicd_v2_checks import product_ownership

    target = _target(tmp_path)
    policy, ref = _policy()
    (target / "build").mkdir()
    (target / "build" / "cov.xml").write_text("foreign")
    write = "import pathlib; pathlib.Path('build/cov.xml').write_text('mine')"
    observed = _run(target, policy, _command(ref, write, expected_products=["build"]))
    ownership = product_ownership(observed)
    assert ownership["foreign"] == ["build/cov.xml"]
    assert ownership["hold"] == "foreign_product_overlap"
    assert "build/cov.xml" not in ownership["owned_paths"]
    # Ownership continues only from an earlier owned layer of the same subject.
    continued = product_ownership(observed, prior_owned=["build/cov.xml"])
    assert continued["owned_paths"] == ["build/cov.xml"]
    assert continued["hold"] is None


def _sha_ref(name: str) -> dict[str, object]:
    return cc.make_ref(name, name.encode())


def test_check_completion_acceptance_tag_is_fixed_and_never_self_certified() -> None:
    from millrace_plus.cicd_v2_checks import completion_acceptance

    common = {
        "subject_ref": _sha_ref("input.json"),
        "command_id": "unit",
        "process_receipt_ref": _sha_ref("checks/unit/receipt.json"),
        "invocation_ref": _sha_ref("core/invocation.json"),
        "completion_ref": _sha_ref("core/completion.json"),
        "guard_ref": _sha_ref("guards/guard_checks.json"),
    }
    record = completion_acceptance(**common)
    assert record["acceptance"]["kind"] == "public_core_completion"
    # The L-Q1 tag never substitutes for CI's fixed public completion tag.
    with pytest.raises(cc.CheckContractError) as other:
        cc.validate_completion_acceptance(record, "selected_stopped_route")
    assert other.value.code == "acceptance_kind_mismatch"
    with pytest.raises(cc.CheckContractError) as self_certified:
        completion_acceptance(
            **(common | {"completion_ref": common["process_receipt_ref"]})
        )
    assert self_certified.value.code == "acceptance_self_certified"


def test_product_layer_lifecycle_links_and_declared_ownership() -> None:
    from millrace_plus.cicd_v2_checks import product_layer

    policy, ref = _policy()
    command = _command(ref, "pass", expected_products=["build"])
    common = {
        "input_id": "a" * 64,
        "materialization_id": None,
        "command": command,
        "policy": policy,
        "policy_ref": ref,
        "before_ref": _sha_ref("before.json"),
    }
    reserved = product_layer(
        **common,
        after_ref=None,
        owned_paths=[],
        completion_ref=None,
        previous_ref=None,
        status="reserved",
    )
    reserved_raw = cc.encode(reserved)
    observed = product_layer(
        **common,
        after_ref=_sha_ref("after.json"),
        owned_paths=["build/cov.xml"],
        completion_ref=None,
        previous_ref=cc.make_ref("layer-1.json", reserved_raw),
        status="observed",
    )
    cc.validate_product_transition(
        reserved_raw, observed["previous_ref"], json.loads(cc.encode(observed))
    )
    observed_raw = cc.encode(observed)
    accepted = product_layer(
        **common,
        after_ref=observed["after_ref"],
        owned_paths=["build/cov.xml"],
        completion_ref=_sha_ref("acceptance.json"),
        previous_ref=cc.make_ref("layer-2.json", observed_raw),
        status="accepted",
    )
    cc.validate_product_transition(observed_raw, accepted["previous_ref"], accepted)
    # Accepted needs pinned acceptance evidence; raw observation cannot accept.
    with pytest.raises(cc.CheckContractError):
        product_layer(
            **common,
            after_ref=observed["after_ref"],
            owned_paths=["build/cov.xml"],
            completion_ref=None,
            previous_ref=accepted["previous_ref"],
            status="accepted",
        )
    with pytest.raises(cc.CheckContractError) as undeclared:
        product_layer(
            **common,
            after_ref=_sha_ref("after.json"),
            owned_paths=["src/calc.py"],
            completion_ref=None,
            previous_ref=_sha_ref("layer-1.json"),
            status="observed",
        )
    assert undeclared.value.code == "undeclared_output"


def test_stacked_layers_reverse_in_order_through_exact_beforeimages(
    tmp_path: Path,
) -> None:
    from millrace_plus.cicd_v2_checks import product_ownership, reverse_products

    target = _target(tmp_path)
    policy, ref = _policy()
    first = _run(
        target,
        policy,
        _command(
            ref,
            (
                "import pathlib; pathlib.Path('build').mkdir(); "
                "pathlib.Path('build/cov.xml').write_text('A')"
            ),
            expected_products=["build"],
        ),
    )
    layer_one = product_ownership(first)
    # Before the second check overwrites an owned file, its exact bytes are saved.
    images = {"build/cov.xml": (target / "build" / "cov.xml").read_bytes()}
    second = _run(
        target,
        policy,
        _command(
            ref, "open('build/cov.xml', 'w').write('B')", expected_products=["build"]
        ),
    )
    layer_two = product_ownership(second, prior_owned=layer_one["owned_paths"])
    assert layer_two["owned_paths"] == ["build/cov.xml"]
    assert layer_two["hold"] is None
    # Without the beforeimage the later layer holds and nothing changes.
    held = reverse_products(
        target,
        layer_two["owned_paths"],
        second["after"]["products"],
        second["before"]["products"],
        _products(target, policy),
    )
    assert (held["status"], held["reason"]) == ("held", "beforeimage_required")
    assert (target / "build" / "cov.xml").read_text() == "B"
    wrong = reverse_products(
        target,
        layer_two["owned_paths"],
        second["after"]["products"],
        second["before"]["products"],
        _products(target, policy),
        {"build/cov.xml": b"not A"},
    )
    assert wrong["reason"] == "beforeimage_required"
    two = reverse_products(
        target,
        layer_two["owned_paths"],
        second["after"]["products"],
        second["before"]["products"],
        _products(target, policy),
        images,
    )
    assert (two["status"], two["restored"], two["removed"]) == (
        "reversed",
        ["build/cov.xml"],
        [],
    )
    assert (target / "build" / "cov.xml").read_text() == "A"
    one = reverse_products(
        target,
        layer_one["owned_paths"],
        first["after"]["products"],
        first["before"]["products"],
        _products(target, policy),
    )
    assert one["status"] == "reversed"
    assert not (target / "build").exists()


def _group_gone(pid: int, seconds: float = 10.0) -> bool:
    import os as _os

    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            _os.killpg(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.02)
    return False


def test_term_before_pid_callback_kills_the_recorded_group_at_once() -> None:
    import subprocess

    from millrace_plus.cicd_v2_checks import TermPropagation

    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
    )
    try:
        guard = TermPropagation()
        with guard:
            guard.on_signal()  # TERM before Core's helper reported the PID.
            assert guard.cancelled() and guard.pid is None
            guard.started(child.pid)
        child.wait(10)
        assert _group_gone(child.pid)
        assert guard.pid is None  # Cleared on exit; a reused group is never hit.
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_wrapper_term_stops_the_nested_check_group_and_reports_uncertain(
    tmp_path: Path,
) -> None:
    import os as _os
    import signal as _signal
    import subprocess

    import millrace_plus

    target = _target(tmp_path)
    policy, ref = _policy()
    command = _command(
        ref,
        (
            "import os, time; open('check.pid', 'w').write(str(os.getpid())); "
            "time.sleep(60)"
        ),
    )
    spec = tmp_path / "command.json"
    spec.write_text(
        json.dumps(
            {
                "command": command,
                "target": str(target),
                "policy": cc.encode(policy.record).decode("utf-8"),
            }
        )
    )
    wrapper = f"""
import json, sys, time
from pathlib import Path
from millrace_plus import check_contracts as cc
from millrace_plus.cicd_v2_checks import TermPropagation, run_command
data = json.loads(Path({str(spec)!r}).read_text())
raw = data["policy"].encode("utf-8")
policy = cc.validate_path_policy(cc.parse(raw))
assert cc.make_ref("path-policy.json", raw) == data["command"]["path_policy_ref"]
with TermPropagation() as guard:
    observed = run_command(
        Path(data["target"]), policy, data["command"],
        {{"command.stdout": 65536, "command.stderr": 65536}},
        base_environment={{"PATH": "/usr/bin:/bin"}}, deadline=time.time() + 60,
        cancelled=guard.cancelled, on_started=guard.started)
print(json.dumps({{"status": observed["status"],
                  "diagnostic": observed["diagnostic"],
                  "facts": observed["process_facts"]}}))
"""
    src = Path(millrace_plus.__file__).resolve().parents[1]
    env = {
        "PATH": _os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONPATH": _os.pathsep.join([str(src), *sys.path]),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    process = subprocess.Popen(
        [sys.executable, "-c", wrapper],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    pid_file = target / "check.pid"
    try:
        end = time.monotonic() + 30
        while not pid_file.exists() or not pid_file.read_text():
            assert process.poll() is None, process.stderr.read()
            assert time.monotonic() < end, "check never started"
            time.sleep(0.02)
        check_pid = int(pid_file.read_text())
        process.send_signal(_signal.SIGTERM)
        out, err = process.communicate(timeout=30)
        assert process.returncode == 0, err
        result = json.loads(out)
        assert result["status"] == "uncertain"
        assert result["diagnostic"] in {"cancelled", "writer_not_closed"}
        assert _group_gone(check_pid)
    finally:
        if process.poll() is None:
            _os.killpg(process.pid, _signal.SIGKILL)
            process.wait()


def test_product_preflight_refuses_existing_foreign_writes_and_owned_drift(tmp_path):
    from millrace_plus.cicd_v2_checks import product_preflight

    policy, ref = _policy()
    target = _target(tmp_path)
    (target / "build").mkdir()
    product = target / "build/result.txt"
    product.write_bytes(b"foreign before launch")
    command = _command(
        ref, "raise AssertionError('must not launch')", expected_products=["build"]
    )
    observed = _products(target, policy)
    with pytest.raises(CicdError, match="foreign_product_overlap"):
        product_preflight(policy, command, observed, {})
    assert product.read_bytes() == b"foreign before launch"
    product_preflight(policy, command, observed, observed)
    product.write_bytes(b"foreign third value")
    with pytest.raises(CicdError, match="product_drift"):
        product_preflight(policy, command, _products(target, policy), observed)
    assert product.read_bytes() == b"foreign third value"
