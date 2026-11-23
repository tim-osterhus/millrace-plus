"""Public CI v2 journeys through the admitted B0 receiver on Linux."""

from __future__ import annotations

import copy
import os
import runpy
import socket
import subprocess
import threading
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from tempfile import mkdtemp

import pytest
from cicd_support import event, new_id, write_json
from cicd_v2_support import initialized
from test_cicd_effects import approval_decision
from test_cicd_effects_workflow import _approve, _configure_local_git, _stop
from test_cicd_workflow import _resume, _run, _start, drive

from millrace_plus import check_contracts as cc
from millrace_plus import cicd_effects as effects
from millrace_plus import cicd_git_commit, cicd_git_push
from millrace_plus.cicd_v2_records import CicdV2Error, validate_config
from millrace_plus.cicd_v2_runtime import V2Home


@pytest.fixture
def b0(request):
    root = os.environ.get("MILLRACE_TEST_B0_RECEIVER")
    if root is None:
        pytest.skip("requires the admitted B0 receiver on omarchy-1")
    return runpy.run_path(str(Path(root) / "probe.py")) | {
        "nodeid": request.node.nodeid
    }


class Journey:
    branch = "refs/heads/delivery"
    remote = "cid1-owned-receiver"

    def __init__(self, root, b0, *, divergent=False, lose_commit_response=False):
        self.root, self.b0 = root, b0
        self.case = Path(mkdtemp(prefix="b1-", dir=b0["ROOT"] / "cases"))
        self.actor_rows = []
        configure_commit = _configure_local_git(
            root, fault="lose_response" if lose_commit_response else None
        )
        git = b0["git"]

        def configure(config):
            configure_commit(config)
            sender = Path(config["target_root"])
            self.ancestor = b0["resolve"](sender, "HEAD")
            git(sender, "commit", "--allow-empty", "-m", "CI input base")
            self.old = b0["resolve"](sender, "HEAD")
            tree = b0["resolve"](sender, "HEAD^{tree}")
            self.other = (
                git(
                    sender,
                    "commit-tree",
                    tree,
                    "-p",
                    self.ancestor,
                    data=b"competing writer\n",
                )
                .decode()
                .strip()
            )
            self.receiver = b0["init_receiver"](
                self.case, sender, self.old, self.other, self.ancestor
            )
            self.alternate = b0["init_receiver"](
                self.case, sender, self.old, self.other, self.ancestor, "alternate.git"
            )
            git(sender, "update-ref", "refs/remotes/origin/delivery", self.old)
            git(sender, "tag", "-a", "unrelated-tag", "-m", "tag at starting tip")
            hooks = self.case / "sender-hooks"
            hooks.mkdir()
            hook = hooks / "pre-push"
            hook.write_text(
                f"#!/bin/sh\ntouch {self.case / 'sender-hook-ran'}\nexit 29\n"
            )
            hook.chmod(0o555)
            for key, value in {
                "remote.origin.url": str(self.receiver),
                "remote.origin.fetch": "+refs/heads/*:refs/remotes/origin/*",
                "core.hooksPath": str(hooks),
                "push.followTags": "true",
                "push.recurseSubmodules": "only",
                "push.negotiate": "true",
                "push.autoSetupRemote": "true",
            }.items():
                git(sender, "config", key, value)
            route = b0["R"]
            cap = write_json(
                root / "push-capability.json",
                {
                    "format": cicd_git_push.CAPABILITY,
                    "remote": self.remote,
                    "receiver": route["directory_identity"](self.receiver),
                    "scripts": [
                        route["tool_identity"](path) for path in route["SCRIPTS"]
                    ],
                    "tools": [
                        route["tool_identity"](Path(path)) for path in route["TOOLS"]
                    ],
                    "qualification_sha256": b0["sha"](
                        (b0["ROOT"] / "run.log").read_bytes()
                    ),
                },
            )
            ref = cc.make_ref(cap.name, cap.read_bytes())
            policy = config["external_policy"]
            policy["effects"].append(
                {
                    "kind": "push",
                    "target": {"remote": self.remote, "branch": self.branch},
                }
            )
            policy["adapters"]["push"] = {
                op: "local-push" for op in ("prepare", "execute", "inspect")
            } | {
                "capabilities": [
                    {"id": capability, "evidence_ref": ref}
                    for capability in ("expected_ref_cas", "exact_inspection")
                ]
            }
            command = next(
                row for row in config["commands"] if row["id"] == "local-git"
            )
            argv = list(command["argv"])
            argv[3] = "millrace_plus.cicd_git_push"
            config["commands"].append(command | {"id": "local-push", "argv": argv})
            if divergent:
                self.move(self.old, self.other)

        self.fx = initialized(root, configure=configure)
        self.home = V2Home(self.fx["home"])
        self.fx["head"] = self.old
        route = b0["R"]
        route["pin_route"](self.receiver, self.home.workspace, self.remote)
        write_json(
            root / "push-case.json",
            {
                "case": str(self.case),
                "receiver": str(self.receiver),
                "node": b0["nodeid"],
            },
        )
        self.input = self.home.submit(event(root, self.old))["input_id"]
        _start(self.fx)
        assert self.report()["hold_reason"] == "delivery_requires_operator", _run(
            self.fx
        )
        self.receiver_before = self.protected()
        self.prefix = self.log()
        self.alternate_before = b0["snapshot"](self.alternate)
        self.commit_approval = _approve(self.fx, self.home, self.input)
        _resume(self.fx)
        if not lose_commit_response:
            self.commit_ready(divergent=divergent)

    def commit_ready(self, *, divergent=False):
        self.commit_refs = self.state()["delivery_refs"]
        self.new = self.b0["resolve"](self.home.target, "HEAD")
        assert self.new != self.old
        assert self.protected() == self.receiver_before and self.log() == self.prefix
        assert self.tip() == (self.other if divergent else self.old)
        self.sender_before = self.b0["snapshot"](self.home.target)
        if not divergent:
            assert self.report()["hold_reason"] == "push_requires_operator", _run(
                self.fx
            )
            assert self.report()["delivery_refs"] == self.commit_refs
            self.decision = approval_decision(self.home, self.input, "push")

    def state(self):
        return self.home.load_state(self.input)

    def report(self):
        state = self.state()
        return self.home.read_ref(state["settlement_ref"] or state["hold_ref"])

    def protected(self):
        return self.b0["snapshot"](
            self.receiver,
            lambda name: name in (self.branch, "logs/" + self.branch)
            or name.startswith("objects/"),
        )

    def tip(self):
        return self.b0["resolve"](self.receiver, self.branch)

    def log(self):
        return (self.receiver / "logs" / self.branch).read_bytes()

    def suffix(self):
        assert self.log().startswith(self.prefix)
        return self.b0["R"]["log_rows"](self.log()[len(self.prefix) :])

    def move(self, old, new):
        assert not hasattr(self, "new") or (old, new) != (self.old, self.new)
        self.b0["git"](
            self.receiver,
            "update-ref",
            "-m",
            "owned race writer",
            self.branch,
            new,
            old,
        )
        assert self.tip() == new
        self.actor_rows.append([old, new])

    @contextmanager
    def race(self, phase: str, action: Callable[[], None]):
        failures = []
        marker = self.case / "race-phase"
        with socket.socket(socket.AF_UNIX) as listener:
            listener.settimeout(20)
            listener.bind(self.b0["R"]["phase_address"](self.case))
            listener.listen()
            marker.write_text(phase)

            def run():
                try:
                    with listener.accept()[0] as channel:
                        assert channel.recv(64) == phase.encode()
                        action()
                        channel.sendall(b"+")
                except BaseException as exc:
                    failures.append(exc)

            actor = threading.Thread(target=run)
            actor.start()
            try:
                yield
            finally:
                actor.join(21)
                marker.unlink()
                assert not actor.is_alive() and not failures, failures
                write_json(
                    self.case / "actor.json",
                    {"phase": phase, "updates": self.actor_rows},
                )

    def operations(self):
        delivery, operations = effects.history(self.home, self.state())
        assert self.home.read_ref(delivery["intent_ref"])["kind"] == "push"
        assert (
            self.state()["delivery_refs"][: len(self.commit_refs)] == self.commit_refs
        )
        return delivery, [row for row in operations if row["status"] == "accepted"]

    def assert_result(self, status, diagnostic, tip, operations):
        delivery, accepted = self.operations()
        assert [row["operation"] for row in accepted] == operations
        result = self.home.read_ref(accepted[-1]["result_ref"])
        assert (result["status"], result["diagnostic"]) == (status, diagnostic)
        observation = self.home.read_ref(result["evidence_refs"][1])
        assert observation["actual_tip"] == self.tip() == tip
        assert (
            observation["ref_locked"] is False and observation["writers_closed"] is True
        )
        for row in accepted:
            process = self.home.read_ref(row["process_ref"])["process_facts"]
            assert process["group_state"] == "closed" and process["root_reaped"]
            completion = self.home.read_ref(row["completion_ref"])["completion"]
            assert completion["effective_writer"] == "stopped"
        return delivery, accepted, result

    def stop_decision(self, kind):
        state = self.state()
        return self.decision | {
            "request_id": new_id(),
            "kind": kind,
            "permission_ref": None,
            "basis_sha256": state["hold_ref"]["sha256"],
            "expected_state_version": state["version"],
        }

    def finish(self):
        assert self.b0["snapshot"](self.home.target) == self.sender_before
        assert self.protected() == self.receiver_before
        assert self.b0["snapshot"](self.alternate) == self.alternate_before
        assert not (self.case / "sender-hook-ran").exists()
        commit = self.home.read_ref(
            self.home.read_ref(self.commit_refs[0])["basis_ref"]
        )
        assert (
            cicd_git_commit.observe(
                self.home,
                commit["target"],
                _run(self.fx)["invocations"][-1]["deadline"],
            )
            == "applied"
        )
        view = _stop(self.fx)
        write_json(self.case / "public-closure.json", view)


def test_installed_push_follows_second_approval(tmp_path, b0):
    case = Journey(tmp_path, b0)
    home = case.home
    stale = case.decision | {"expected_state_version": case.state()["version"] - 1}
    with pytest.raises(CicdV2Error, match="stale_state"):
        home.decide(stale)
    permission = home.read_ref(case.decision["permission_ref"])
    approval = home.read_ref(permission["approval_ref"])
    for key, value in (("remote", "different"), ("branch", "refs/heads/different")):
        target = permission["effects"][0]["target"] | {key: value}
        selected = [{"kind": "push", "target": target}]
        wrong = permission | {
            "effects": selected,
            "approval_ref": home.retain(approval | {"effects": selected}),
        }
        with pytest.raises(CicdV2Error, match="action_permission_mismatch"):
            home.decide(case.decision | {"permission_ref": home.retain(wrong)})
    with pytest.raises(CicdV2Error, match="action_permission_mismatch"):
        home.decide(
            case.decision | {"permission_ref": case.commit_approval["permission_ref"]}
        )
    without_basis = home.retain(
        permission | {"approval_ref": home.retain(approval | {"evidence_refs": []})}
    )
    with pytest.raises(CicdV2Error, match="push_approval_basis_missing"):
        home.decide(case.decision | {"permission_ref": without_basis})
    for ids in (["exact_inspection"], ["exact_inspection", "arbitrary"]):
        config = copy.deepcopy(home.config)
        adapter = config["external_policy"]["adapters"]["push"]
        evidence = adapter["capabilities"][0]["evidence_ref"]
        adapter["capabilities"] = [{"id": key, "evidence_ref": evidence} for key in ids]
        with pytest.raises(CicdV2Error):
            validate_config(config)
    custom = copy.deepcopy(home.config)
    del custom["external_policy"]["adapters"]["commit"]["backend"]
    with pytest.raises(CicdV2Error, match="unsupported_until_full_ci_d1"):
        validate_config(custom)
    assert case.tip() == case.old and case.suffix() == []
    receipt = home.decide(case.decision)
    assert home.decide(case.decision) == receipt
    assert case.tip() == case.old and case.suffix() == []
    drive(case.fx, _resume)
    assert case.report()["status"] == "published"
    assert home.decide(case.decision) == receipt
    delivery, accepted, result = case.assert_result(
        "applied", None, case.new, ["prepare", "execute"]
    )
    assert case.suffix() == [[case.old, case.new]]
    request = home.read_ref(delivery["intent_ref"])
    assert request["prior_effect_refs"] == [case.commit_refs[-1]]
    assert accepted[-1]["request_ref"] == delivery["intent_ref"]
    assert home.resolve_ref(delivery["intent_ref"]).stat().st_mode & 0o222 == 0
    guard = home.read_ref(result["evidence_refs"][-1])
    assert guard["execute_sha256"] == delivery["intent_ref"]["sha256"]
    assert guard["basis_ref"] == request["evidence_refs"][0]
    assert guard["update"] == [case.old, case.new, case.branch]
    with pytest.raises(CicdV2Error, match="invalid_push_evidence"):
        effects._result_evidence(
            home,
            result | {"diagnostic": "push_superseded"},
            request,
            accepted[-1]["retained_evidence_refs"],
        )
    changed = home.retain(request | {"prior_effect_refs": [case.commit_refs[-2]]})
    wrong_delivery = home.retain(delivery | {"intent_ref": changed})
    refs = list(case.state()["delivery_refs"])
    refs[len(case.commit_refs)] = wrong_delivery
    with pytest.raises(CicdV2Error, match="push_approval_basis_changed"):
        effects.history(home, case.state() | {"delivery_refs": refs})
    case.finish()


def test_installed_push_requires_inspection_after_local_commit_response_loss(
    tmp_path, b0
):
    case = Journey(tmp_path, b0, lose_commit_response=True)
    home = case.home
    held = case.state()
    assert case.report()["hold_reason"] == "effect_inspection_required"
    assert case.report()["status"] == "checked_patch"
    assert effects.disposition(home, held) == "inspect"
    assert effects.basis_ref(home, case.input, push=True) is None
    assert case.tip() == case.old and case.suffix() == []
    delivery, operations = effects.history(home, held)
    execute = operations[-1]
    assert execute["operation"] == "execute" and execute["result_ref"] is None
    request = home.read_ref(delivery["intent_ref"])
    committed = b0["resolve"](home.target, "HEAD")
    assert committed != case.old
    assert b0["git"](
        home.target, "rev-list", "--count", f"{case.old}..{committed}"
    ) == b"1\n"
    sender_after_commit = b0["snapshot"](home.target)
    write_json(
        case.case / "commit-response-loss.json",
        {
            "hold_ref": held["hold_ref"],
            "execute_ref": held["delivery_refs"][-1],
            "sender_head": committed,
            "receiver_head": case.tip(),
        },
    )
    close = case.commit_approval | {
        "request_id": new_id(),
        "kind": "close",
        "permission_ref": None,
        "basis_sha256": held["hold_ref"]["sha256"],
        "expected_state_version": held["version"],
    }
    with pytest.raises(CicdV2Error, match="effect_unresolved"):
        home.decide(close)

    _resume(case.fx)
    case.commit_ready()
    assert case.new == committed
    assert case.sender_before == sender_after_commit
    _, operations = effects.history(home, case.state())
    assert sum(
        row["operation"] == "execute" and row["status"] == "reserved"
        for row in operations
    ) == 1
    inspected = operations[-1]
    assert inspected["operation"] == "inspect" and inspected["status"] == "accepted"
    assert home.read_ref(inspected["request_ref"]) == request | {"operation": "inspect"}
    assert home.read_ref(inspected["result_ref"])["observed_head"] == committed
    assert case.tip() == case.old and case.suffix() == []

    home.decide(case.decision)
    assert case.tip() == case.old
    drive(case.fx, _resume)
    assert case.report()["status"] == "published"
    push, _, _ = case.assert_result("applied", None, committed, ["prepare", "execute"])
    push_request = home.read_ref(push["intent_ref"])
    assert push_request["prior_effect_refs"] == [case.commit_refs[-1]]
    assert case.suffix() == [[case.old, committed]]
    case.finish()


@pytest.mark.parametrize("later", [None, "advance", "return"])
def test_installed_push_recovers_on_new_public_run(tmp_path, b0, later):
    case = Journey(tmp_path, b0)
    case.home.decide(case.decision)
    (case.case / "drop-response").touch()
    _resume(case.fx)
    assert case.report()["hold_reason"] == "push_response_unavailable"
    assert case.tip() == case.new
    old_run = _stop(case.fx)
    write_json(case.case / "old-public-closure.json", old_run)
    if later == "advance":
        tree = b0["resolve"](case.receiver, f"{case.new}^{{tree}}")
        tip = (
            b0["git"](
                case.receiver,
                "commit-tree",
                tree,
                "-p",
                case.new,
                data=b"later advance\n",
            )
            .decode()
            .strip()
        )
        case.move(case.new, tip)
    elif later == "return":
        case.move(case.new, case.other)
        case.move(case.other, case.new)
    tip = case.tip()
    before_inspect = b0["snapshot"](case.receiver)
    drive(case.fx, _start)
    assert b0["snapshot"](case.receiver) == before_inspect
    assert _run(case.fx)["id"] != old_run["id"]
    diagnostic = "push_superseded" if later else None
    case.assert_result("applied", diagnostic, tip, ["prepare", "execute", "inspect"])
    assert case.suffix() == [[case.old, case.new], *case.actor_rows]
    if later:
        assert case.report()["hold_reason"] == diagnostic
        kind = "close" if later == "advance" else "cancel"
        case.home.decide(case.stop_decision(kind))
        drive(case.fx, _resume)
        assert case.home.task(case.input)["status"] == (
            "succeeded" if kind == "close" else "cancelled"
        )
        assert case.report()["status"] == (
            "checked_patch" if kind == "close" else "cancelled"
        )
        assert case.tip() == tip
    else:
        assert case.report()["status"] == "published"
    case.finish()


@pytest.mark.parametrize(
    "race", ["before_advertise", "before_guard", "after_admit", "aba"]
)
def test_installed_push_retains_race_disposition(tmp_path, b0, race):
    case = Journey(tmp_path, b0)
    case.home.decide(case.decision)

    def movement():
        earlier = case.ancestor if race == "before_advertise" else case.other
        case.move(case.old, earlier)
        if race in {"before_guard", "aba"}:
            case.move(case.other, case.old)

    with case.race("after_admit" if race == "aba" else race, movement):
        _resume(case.fx)
    if race == "aba":
        assert case.report()["hold_reason"] == "push_inspection_uncertain"
        _resume(case.fx)
        _, accepted, _ = case.assert_result(
            "uncertain",
            "push_inspection_uncertain",
            case.new,
            ["prepare", "execute", "inspect"],
        )
        assert case.suffix() == [*case.actor_rows, [case.old, case.new]]
        for kind in ("close", "cancel"):
            with pytest.raises(CicdV2Error, match="effect_unresolved"):
                case.home.decide(case.stop_decision(kind))
        _resume(case.fx)
        assert case.operations()[1][1] == accepted[1]
        assert [row["operation"] for row in case.operations()[1]].count("execute") == 1
        assert case.home.task(case.input)["status"] == "held"
    else:
        _resume(case.fx)
        expected = case.actor_rows[-1][1]
        _, accepted, result = case.assert_result(
            "not_applied",
            "push_ref_moved",
            expected,
            ["prepare", "execute", "inspect"],
        )
        inspected = accepted[-1]
        with pytest.raises(CicdV2Error, match="invalid_push_evidence"):
            effects._result_evidence(
                case.home, result | {"diagnostic": None},
                case.home.read_ref(inspected["request_ref"]),
                inspected["retained_evidence_refs"],
            )
        assert case.report()["hold_reason"] == "push_ref_moved"
        case.home.decide(case.stop_decision("close"))
        drive(case.fx, _resume)
        assert case.home.task(case.input)["status"] == "succeeded"
        assert case.report()["status"] == "checked_patch"
        assert case.suffix() == case.actor_rows and case.tip() == expected
    case.finish()


@pytest.mark.parametrize("movement", [False, True])
def test_installed_push_retries_only_after_exact_absence(tmp_path, b0, movement):
    case = Journey(tmp_path, b0)
    case.home.decide(case.decision)
    lock_path = case.receiver / (case.branch + ".lock")
    with subprocess.Popen(
        [b0["R"]["GIT"], "-C", str(case.receiver), "update-ref", "--stdin"],
        env=b0["ENV"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as writer:

        def hold_lock():
            update = f"update {case.branch} {case.other} {case.old}"
            writer.stdin.write(f"start\n{update}\nprepare\n".encode())
            writer.stdin.flush()
            assert writer.stdout.readline() == b"start: ok\n"
            assert writer.stdout.readline() == b"prepare: ok\n"
            assert lock_path.exists()

        (case.case / "drop-response").touch()
        try:
            with case.race("after_admit", hold_lock):
                _resume(case.fx)
            assert case.tip() == case.old and case.log() == case.prefix
            assert case.report()["hold_reason"] == "push_response_unavailable"
            _resume(case.fx)
            assert case.report()["hold_reason"] == "push_receiver_locked"
            assert [row["operation"] for row in case.operations()[1]].count(
                "execute"
            ) == 1
        finally:
            stdout, stderr = writer.communicate(b"abort\n", timeout=20)
            assert writer.returncode == 0 and stdout == b"abort: ok\n", (stdout, stderr)
    assert not lock_path.exists()
    write_json(
        case.case / "lock-closure.json",
        {"pid": writer.pid, "status": writer.returncode},
    )
    (case.case / "drop-response").unlink()
    if movement:
        case.move(case.old, case.other)
        case.move(case.other, case.old)
        _resume(case.fx)
        case.assert_result(
            "not_applied",
            "push_ref_moved",
            case.old,
            ["prepare", "execute", "inspect", "inspect"],
        )
        case.home.decide(case.stop_decision("cancel"))
        drive(case.fx, _resume)
        assert case.report()["status"] == "cancelled"
        assert case.suffix() == case.actor_rows
    else:
        drive(case.fx, _resume)
        delivery, accepted, _ = case.assert_result(
            "applied",
            None,
            case.new,
            ["prepare", "execute", "inspect", "inspect", "execute"],
        )
        assert (
            accepted[-1]["request_ref"]
            == accepted[1]["request_ref"]
            == delivery["intent_ref"]
        )
        absent = case.home.read_ref(accepted[-2]["result_ref"])
        assert (absent["status"], absent["diagnostic"]) == ("not_applied", None)
        assert case.home.resolve_ref(absent["evidence_refs"][3]).read_bytes() == b""
        assert case.report()["status"] == "published"
        assert case.suffix() == [[case.old, case.new]]
    case.finish()


def test_installed_push_refuses_non_fast_forward_before_reservation(tmp_path, b0):
    case = Journey(tmp_path, b0, divergent=True)
    result = b0["R"]["git_command"](
        case.home.target,
        ("merge-base", "--is-ancestor", case.other, case.new),
    )
    assert result.returncode == 1
    assert case.report()["hold_reason"] == "push_receiver_tip_mismatch"
    assert case.tip() == case.other
    assert len(effects._segments(case.home, case.state())) == 1
    assert effects.basis_ref(case.home, case.input, push=True) is None
    case.finish()


@pytest.mark.parametrize("kind", ["close", "cancel"])
def test_installed_push_stop_preserves_closed_local_commit(tmp_path, b0, kind):
    case = Journey(tmp_path, b0)
    if kind == "cancel":
        case.home.decide(case.decision)
    case.home.decide(case.stop_decision(kind))
    drive(case.fx, _resume)
    assert case.home.task(case.input)["status"] == (
        "succeeded" if kind == "close" else "cancelled"
    )
    assert case.tip() == case.old and case.suffix() == []
    assert not list(case.case.glob("guard-*.json"))
    if kind == "close":
        assert case.report()["delivery_refs"] == case.commit_refs
    else:
        _, operations = case.operations()
        assert operations == []
    case.finish()
