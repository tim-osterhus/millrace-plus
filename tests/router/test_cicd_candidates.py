"""CI v2 classified source snapshots under the shared captured path policy.

These exercise `cicd_source.snapshot_policy` directly on real filesystem trees:
baseline links stay opaque, environment paths are never traversed, product
paths never enter source identity, and Git/special shapes are reported for a
hold rather than followed or changed.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
from pathlib import Path

import pytest

from millrace_plus import check_contracts as cc
from millrace_plus.cicd_records import CicdError
from millrace_plus.cicd_source import (
    OWNER,
    baseline_links,
    snapshot_policy,
    source_identity,
)


def _policy(*rules: tuple[str, list[str]]) -> cc.PathPolicy:
    record = {
        "format": cc.PATH_POLICY,
        "rules": [
            {
                "surface": surface,
                "prefixes": prefixes,
                "reason": "test-owned classification",
                "origin": "test fixture",
                "override": "capture another policy before init",
            }
            for surface, prefixes in rules
        ],
    }
    return cc.validate_path_policy(json.loads(cc.encode(record)))


def _tree(root: Path) -> Path:
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (root / OWNER).write_text("{}\n")
    (root / "src").mkdir()
    (root / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (root / "src" / "__pycache__").mkdir()
    (root / "src" / "__pycache__" / "calc.pyc").write_bytes(b"\0cache")
    (root / ".venv" / "lib").mkdir(parents=True)
    (root / ".venv" / "lib" / "dep.py").write_text("x = 1\n")
    (root / "build").mkdir()
    (root / "build" / "coverage.xml").write_text("<coverage/>\n")
    os.symlink("src/calc.py", root / "calc-link")
    return root


def test_classified_snapshot_keeps_source_links_products_and_environment(
    tmp_path: Path,
) -> None:
    target = _tree(tmp_path / "target")
    policy = _policy(
        ("environment_paths", [*cc.CACHE_TOKENS, ".venv"]),
        ("product_paths", ["build"]),
    )
    view = snapshot_policy(target, policy)
    # `.git` and the owner marker are structure, never snapshot entries.
    assert set(view["source"]) == {"src/calc.py"}
    assert view["environment"] == {".venv": "directory", "src/__pycache__": "directory"}
    assert view["products"] == {
        "build": {"kind": "directory"},
        "build/coverage.xml": {
            "kind": "file",
            "mode": stat.S_IMODE(os.lstat(target / "build/coverage.xml").st_mode),
            "sha256": hashlib.sha256(b"<coverage/>\n").hexdigest(),
        },
    }
    raw = os.readlink(os.fsencode(target / "calc-link"))
    assert view["links"] == {
        "calc-link": {
            "mode": os.lstat(target / "calc-link").st_mode,
            "target": base64.b64encode(raw).decode("ascii"),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    }
    assert baseline_links(view) == [{"path": "calc-link"} | view["links"]["calc-link"]]
    assert view["gitlinks"] == view["special"] == view["unsupported"] == []


def test_products_and_environment_never_change_source_identity(tmp_path: Path) -> None:
    target = _tree(tmp_path / "target")
    policy = _policy(
        ("environment_paths", [*cc.CACHE_TOKENS, ".venv"]),
        ("product_paths", ["build"]),
    )
    before = source_identity(snapshot_policy(target, policy))
    (target / "build" / "coverage.xml").write_text("<coverage lines='9'/>\n")
    (target / "build" / "new.txt").write_text("generated\n")
    (target / ".venv" / "lib" / "dep.py").write_text("x = 2\n")
    (target / "src" / "__pycache__" / "calc.pyc").write_bytes(b"\0other")
    assert source_identity(snapshot_policy(target, policy)) == before
    # A real source edit or a changed baseline link always changes identity.
    (target / "src" / "calc.py").write_text("def add(a, b):\n    return b + a\n")
    edited = source_identity(snapshot_policy(target, policy))
    assert edited != before
    os.remove(target / "calc-link")
    os.symlink("src/other.py", target / "calc-link")
    assert source_identity(snapshot_policy(target, policy)) != edited


def test_unclassified_outputs_remain_source(tmp_path: Path) -> None:
    """No hidden exclusion: without a rule, `build/` and `.venv/` are source."""
    target = _tree(tmp_path / "target")
    stock = cc.validate_path_policy(cc.parse(cc.stock_path_policy()))
    view = snapshot_policy(target, stock)
    assert {"build/coverage.xml", ".venv/lib/dep.py", "src/calc.py"} <= set(
        view["source"]
    )
    assert view["environment"] == {"src/__pycache__": "directory"}
    assert view["products"] == {}


def test_gitlink_and_special_source_entries_are_reported_not_followed(
    tmp_path: Path,
) -> None:
    target = _tree(tmp_path / "target")
    (target / "vendor" / "sub").mkdir(parents=True)
    (target / "vendor" / "sub" / ".git").write_text("gitdir: ../../.git/modules/sub\n")
    (target / "vendor" / "sub" / "lib.py").write_text("y = 1\n")
    os.mkfifo(target / "src" / "pipe")
    policy = cc.validate_path_policy(cc.parse(cc.stock_path_policy()))
    before = {p: p.read_bytes() for p in target.rglob("*") if p.is_file()}
    view = snapshot_policy(target, policy)
    assert view["gitlinks"] == ["vendor/sub"]
    assert view["special"] == ["src/pipe"]
    assert {p: p.read_bytes() for p in target.rglob("*") if p.is_file()} == before


def test_product_link_is_unsupported_and_selector_conflict_refuses(
    tmp_path: Path,
) -> None:
    target = _tree(tmp_path / "target")
    os.symlink("../src", target / "build" / "src-link")
    view = snapshot_policy(target, _policy(("product_paths", ["build"])))
    assert view["unsupported"] == ["build/src-link"]
    assert "build/src-link" not in view["links"]
    (target / "build" / "__pycache__").mkdir()
    conflicting = _policy(
        ("environment_paths", list(cc.CACHE_TOKENS)), ("product_paths", ["build"])
    )
    with pytest.raises(cc.CheckContractError) as refused:
        snapshot_policy(target, conflicting)
    assert refused.value.code == "path_policy_conflict"


@pytest.mark.parametrize("surface", ["snapshot.entries", "snapshot.bytes"])
def test_operator_snapshot_guard_holds_and_omitted_guard_is_unbounded(
    tmp_path: Path, surface: str
) -> None:
    target = _tree(tmp_path / "target")
    policy = cc.validate_path_policy(cc.parse(cc.stock_path_policy()))
    assert snapshot_policy(target, policy)["source"]
    with pytest.raises(CicdError) as held:
        snapshot_policy(target, policy, {surface: 1})
    assert held.value.code == "snapshot_guard_exceeded"


def _view(**row_changes: object) -> dict[str, object]:
    row = {
        "id": "inv-2",
        "run_id": "run-1",
        "revision_id": "rev-1",
        "plane_id": "main",
        "node": "checks",
        "attempt": 2,
        "status": "active",
        "pid": 4242,
        "cwd": "/home/ci/project/main",
        "deadline": 1893456000.5,
        "argv": ["python3", "/home/ci/project/main/scripts/step.py", "checks"],
    }
    other = {
        "id": "inv-1",
        "run_id": "run-1",
        "revision_id": "rev-1",
        "plane_id": "main",
        "node": "freeze",
        "attempt": 1,
        "status": "completed",
        "pid": 4100,
        "cwd": "/home/ci/project/main",
        "deadline": 1893450000.0,
        "argv": ["python3", "/home/ci/project/main/scripts/step.py", "freeze"],
    }
    return {
        "id": "run-1",
        "revision_id": "rev-1",
        "planes": {"main": {"active_invocation": "inv-2"}},
        "invocations": [other, row | row_changes],
    }


_BIND = {
    "instance_id": "inst-1",
    "state_home": Path("/home/ci/state"),
    "controls_root": Path("/home/ci/project/controls"),
    "expected_plane": "main",
    "expected_node": "checks",
    "script": Path("/home/ci/project/main/scripts/step.py"),
    "pid": 4242,
    "cwd": Path("/home/ci/project/main"),
}


def test_invocation_context_matches_one_active_public_invocation() -> None:
    from millrace_plus.cicd_candidates import CONTEXT_FIELDS, match_invocation

    view = _view()
    context = match_invocation(view, **_BIND)
    assert tuple(context) == CONTEXT_FIELDS
    assert (context["invocation_id"], context["attempt"], context["node"]) == (
        "inv-2",
        2,
        "checks",
    )
    assert context["deadline"] == 1893456000.5
    from millrace_plus.local_intake import canonical

    assert context["public_run_ref"] == hashlib.sha256(canonical(view)).hexdigest()


@pytest.mark.parametrize(
    ("row", "binding", "code"),
    [
        ({"node": "review"}, {}, "invocation_node_mismatch"),
        ({"pid": None}, {}, "pid_unpublished"),
        ({"pid": 999}, {}, "invocation_pid_mismatch"),
        ({"pid": True}, {"pid": 1}, "invocation_pid_mismatch"),
        ({"cwd": "/elsewhere"}, {}, "invocation_cwd_mismatch"),
        ({"argv": ["python3", "/tmp/other.py"]}, {}, "invocation_argv_mismatch"),
        ({"deadline": None}, {}, "invocation_deadline_missing"),
        ({"deadline": float("inf")}, {}, "invocation_deadline_missing"),
        ({}, {"expected_plane": "side"}, "invocation_not_active"),
    ],
)
def test_invocation_context_refuses_any_identity_mismatch(
    row: dict[str, object], binding: dict[str, object], code: str
) -> None:
    from millrace_plus.cicd_candidates import match_invocation

    with pytest.raises(CicdError) as refused:
        match_invocation(_view(**row), **(_BIND | binding))
    assert refused.value.code == code


def test_two_active_rows_or_stale_pointer_are_ambiguous() -> None:
    from millrace_plus.cicd_candidates import match_invocation

    view = _view()
    view["invocations"][0]["status"] = "active"
    with pytest.raises(CicdError) as two:
        match_invocation(view, **_BIND)
    assert two.value.code == "invocation_ambiguous"
    stale = _view()
    stale["planes"]["main"]["active_invocation"] = "inv-1"
    with pytest.raises(CicdError) as pointer:
        match_invocation(stale, **_BIND)
    assert pointer.value.code == "invocation_ambiguous"


HEAD = "a" * 40


def _manifest_of(target: Path, bundle: Path) -> dict[str, object]:
    from millrace_plus.cicd_candidates import retain_payloads, tree_manifest

    raw = cc.stock_path_policy()
    view = snapshot_policy(target, cc.validate_path_policy(cc.parse(raw)))
    refs = retain_payloads(target, view, bundle)
    return tree_manifest(
        view,
        repository="acme/calc",
        anchor_head=HEAD,
        policy_ref=cc.make_ref("path-policy.json", raw),
        payload_refs=refs,
    )


def test_tree_manifest_retains_exact_payloads_and_opaque_links(tmp_path: Path) -> None:
    from millrace_plus.cicd_v2_records import tree_digest

    target = _tree(tmp_path / "target")
    os.chmod(target / "src" / "calc.py", 0o755)
    bundle = tmp_path / "bundle"
    tree = _manifest_of(target, bundle)
    by_path = {entry["path"]: entry for entry in tree["entries"]}
    calc = by_path["src/calc.py"]
    assert calc["mode"] == stat.S_IFREG | 0o755
    payload = bundle / calc["payload_ref"]["path"]
    assert payload.read_bytes() == (target / "src" / "calc.py").read_bytes()
    assert "src/__pycache__/calc.pyc" not in by_path  # environment, never source
    assert [link["path"] for link in tree["baseline_links"]] == ["calc-link"]
    # Rebuilding the same tree is byte-identical, so the digest is stable.
    assert tree_digest(_manifest_of(target, bundle)) == tree_digest(tree)


def test_tree_delta_lists_exact_changes_and_holds_link_drift(tmp_path: Path) -> None:
    from millrace_plus.cicd_candidates import tree_delta

    target = _tree(tmp_path / "target")
    bundle = tmp_path / "bundle"
    base = _manifest_of(target, bundle)
    assert tree_delta(base, base)["changes"] == []
    (target / "src" / "calc.py").write_text("def add(a, b):\n    return b + a\n")
    (target / "src" / "new.py").write_text("x = 1\n")
    candidate = _manifest_of(target, bundle)
    delta = tree_delta(base, candidate)
    paths = [change["path"] for change in delta["changes"]]
    assert paths == ["src/calc.py", "src/new.py"]
    assert delta["changes"][1]["before"] is None
    os.remove(target / "calc-link")
    os.symlink("src/new.py", target / "calc-link")
    with pytest.raises(CicdError) as held:
        tree_delta(base, _manifest_of(target, bundle))
    assert held.value.code == "baseline_link_changed"


def test_gitlink_and_special_source_entries_hold_tree_capture(tmp_path: Path) -> None:
    target = _tree(tmp_path / "target")
    (target / "vendor" / "sub").mkdir(parents=True)
    (target / "vendor" / "sub" / ".git").write_text("gitdir: x\n")
    with pytest.raises(CicdError) as gitlink:
        _manifest_of(target, tmp_path / "bundle")
    assert gitlink.value.code == "gitlink_source_entry"
    (target / "vendor" / "sub" / ".git").unlink()
    os.mkfifo(target / "src" / "pipe")
    with pytest.raises(CicdError) as special:
        _manifest_of(target, tmp_path / "bundle")
    assert special.value.code == "special_source_entry"


def test_export_applies_only_the_job_delta_to_the_prior_logical_tree(
    tmp_path: Path,
) -> None:
    from millrace_plus.cicd_candidates import apply_job_delta, tree_delta

    target = _tree(tmp_path / "target")
    (target / "src" / "other.py").write_text("y = 1\n")
    bundle = tmp_path / "bundle"
    prior = _manifest_of(target, bundle)  # accepted logical base
    # Foreign preexisting edit present at task start; never part of the job.
    (target / "src" / "other.py").write_text("y = 2  # user's own edit\n")
    start = _manifest_of(target, bundle)
    (target / "src" / "calc.py").write_text("def add(a, b):\n    return a + b + 0\n")
    end = _manifest_of(target, bundle)
    job = tree_delta(start, end)
    candidate = apply_job_delta(prior, job, ["src/other.py"])
    by_path = {entry["path"]: entry for entry in candidate["entries"]}
    prior_by_path = {entry["path"]: entry for entry in prior["entries"]}
    assert by_path["src/other.py"] == prior_by_path["src/other.py"]
    assert (
        by_path["src/calc.py"]
        == {entry["path"]: entry for entry in end["entries"]}["src/calc.py"]
    )
    assert tree_delta(prior, candidate)["changes"] == job["changes"]


def test_export_refuses_foreign_overlap_and_stale_logical_base(tmp_path: Path) -> None:
    from millrace_plus.cicd_candidates import apply_job_delta, tree_delta

    target = _tree(tmp_path / "target")
    bundle = tmp_path / "bundle"
    prior = _manifest_of(target, bundle)
    (target / "src" / "calc.py").write_text(
        "# user's edit\n" + (target / "src" / "calc.py").read_text()
    )
    start = _manifest_of(target, bundle)
    (target / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    job = tree_delta(start, _manifest_of(target, bundle))
    with pytest.raises(CicdError) as overlap:
        apply_job_delta(prior, job, ["src/calc.py"])
    assert overlap.value.code == "foreign_overlap"
    # Without the foreign declaration, the start no longer equals the logical base.
    with pytest.raises(CicdError) as stale:
        apply_job_delta(prior, job, [])
    assert stale.value.code == "logical_base_mismatch"


REPAIRED = "def add(a, b):\n    return b + a  # repaired\n"


def _start_end(tmp_path: Path) -> tuple[Path, Path, dict[str, object], bytes]:
    from millrace_plus.cicd_candidates import tree_delta

    target = _tree(tmp_path / "target")
    (target / "src" / "gone.py").write_text("old = True\n")
    bundle = tmp_path / "bundle"
    start = _manifest_of(target, bundle)
    original = (target / "src" / "calc.py").read_bytes()
    (target / "src" / "calc.py").write_text(REPAIRED)
    (target / "src" / "gone.py").unlink()
    (target / "src" / "pkg").mkdir()
    (target / "src" / "pkg" / "new.py").write_text("n = 1\n")
    os.chmod(target / "src" / "pkg" / "new.py", 0o755)
    end = _manifest_of(target, bundle)
    return target, bundle, tree_delta(start, end), original


def test_restore_then_import_round_trips_exact_bytes_and_modes(tmp_path: Path) -> None:
    from millrace_plus.cicd_candidates import apply_delta

    target, bundle, delta, original = _start_end(tmp_path)
    after = {p: (target / p).read_bytes() for p in ("src/calc.py", "src/pkg/new.py")}
    restored = apply_delta(target, delta, bundle, inverse=True)
    assert sorted(restored["applied"]) == [
        "src/calc.py",
        "src/gone.py",
        "src/pkg/new.py",
    ]
    assert (target / "src" / "calc.py").read_bytes() == original
    assert (target / "src" / "gone.py").read_text() == "old = True\n"
    assert not (target / "src" / "pkg" / "new.py").exists()
    imported = apply_delta(target, delta, bundle)
    assert imported["already"] == []
    assert {p: (target / p).read_bytes() for p in after} == after
    assert stat.S_IMODE(os.stat(target / "src" / "pkg" / "new.py").st_mode) == 0o755
    assert not (target / "src" / "gone.py").exists()
    # Replay after completion is a no-op: every path is already its destination.
    assert apply_delta(target, delta, bundle) == {
        "applied": [],
        "already": ["src/calc.py", "src/gone.py", "src/pkg/new.py"],
    }


def test_partial_write_reconciles_and_third_value_holds_untouched(
    tmp_path: Path,
) -> None:
    from millrace_plus.cicd_candidates import apply_delta

    target, bundle, delta, original = _start_end(tmp_path)
    apply_delta(target, delta, bundle, inverse=True)
    # Simulate a crash after one path was written: it is exactly its after image.
    (target / "src" / "calc.py").write_text(REPAIRED)
    resumed = apply_delta(target, delta, bundle)
    assert resumed["already"] == ["src/calc.py"]
    assert sorted(resumed["applied"]) == ["src/gone.py", "src/pkg/new.py"]
    apply_delta(target, delta, bundle, inverse=True)
    (target / "src" / "calc.py").write_text("# foreign third value\n")
    with pytest.raises(CicdError) as drift:
        apply_delta(target, delta, bundle)
    assert drift.value.code == "materialization_drift"
    # Nothing else moved: the other paths are still their before images.
    assert (target / "src" / "gone.py").exists()
    assert not (target / "src" / "pkg" / "new.py").exists()
    assert (target / "src" / "calc.py").read_text() == "# foreign third value\n"


def test_link_parent_and_missing_payload_hold_before_any_write(tmp_path: Path) -> None:
    from millrace_plus.cicd_candidates import apply_delta

    target, bundle, delta, original = _start_end(tmp_path)
    apply_delta(target, delta, bundle, inverse=True)
    (target / "src" / "pkg").rmdir()  # Directories are never tree entries.
    (tmp_path / "elsewhere").mkdir()
    os.symlink(tmp_path / "elsewhere", target / "src" / "pkg")
    with pytest.raises(CicdError) as linked:
        apply_delta(target, delta, bundle)
    assert linked.value.code == "link_parent"
    assert list((tmp_path / "elsewhere").iterdir()) == []
    os.unlink(target / "src" / "pkg")
    for payload in (bundle / "payloads").iterdir():
        if payload.read_bytes() == b"n = 1\n":
            payload.unlink()
    with pytest.raises(CicdError) as missing:
        apply_delta(target, delta, bundle)
    assert missing.value.code == "payload_missing"
    assert (target / "src" / "calc.py").read_bytes() == original


def _git(root: Path, *args: str) -> str:
    import subprocess

    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(root),
        "GIT_CONFIG_NOSYSTEM": "1",
        "LC_ALL": "C",
    }
    done = subprocess.run(
        [
            "git",
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@invalid",
            "-c",
            "init.defaultBranch=main",
            *args,
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def test_foreign_dirty_paths_cover_staged_unstaged_untracked_ignored_and_deleted(
    tmp_path: Path,
) -> None:
    from millrace_plus.cicd_source import foreign_dirty_paths

    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    _git(root, "init", "-q")
    files = {
        "src/calc.py": "a = 1\n",
        "src/run.sh": "echo\n",
        "src/keep.py": "k\n",
        "src/staged.py": "s\n",
        "src/gone.py": "g\n",
        ".gitignore": "*.log\n",
    }
    for name, text in files.items():
        (root / name).write_text(text)
    os.symlink("calc.py", root / "src" / "link")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    head = _git(root, "rev-parse", "HEAD")
    policy = cc.validate_path_policy(cc.parse(cc.stock_path_policy()))
    clean = snapshot_policy(root, policy)
    assert foreign_dirty_paths(root, head, clean, 30) == []
    (root / "src" / "calc.py").write_text("a = 2\n")  # unstaged edit
    (root / "src" / "staged.py").write_text("s2\n")
    _git(root, "add", "src/staged.py")  # staged edit
    (root / "src" / "new.py").write_text("n\n")  # untracked
    (root / "debug.log").write_text("ignored but foreign\n")  # ignored untracked
    (root / "src" / "gone.py").unlink()  # tracked, now absent
    os.chmod(root / "src" / "run.sh", 0o755)  # executable bit only
    os.remove(root / "src" / "link")
    os.symlink("keep.py", root / "src" / "link")  # retargeted link
    (root / "src" / "__pycache__").mkdir()
    (root / "src" / "__pycache__" / "x.pyc").write_bytes(b"\0")  # environment
    dirty = foreign_dirty_paths(root, head, snapshot_policy(root, policy), 30)
    assert dirty == [
        "debug.log",
        "src/calc.py",
        "src/gone.py",
        "src/link",
        "src/new.py",
        "src/run.sh",
        "src/staged.py",
    ]
    # Read-only: nothing was staged or committed by the classification.
    assert _git(root, "rev-parse", "HEAD") == head
    assert _git(root, "diff", "--cached", "--name-only") == "src/staged.py"


def test_before_manifest_pins_task_start_facts_and_foreign_dirty_paths(
    tmp_path: Path,
) -> None:
    from millrace_plus.cicd_candidates import (
        before_manifest,
        retain_payloads,
        target_record,
    )
    from millrace_plus.cicd_v2_records import CicdV2Error

    target = _tree(tmp_path / "target")
    raw = cc.stock_path_policy()
    view = snapshot_policy(target, cc.validate_path_policy(cc.parse(raw)))
    refs = retain_payloads(target, view, tmp_path / "bundle")
    owner = cc.make_ref("owner.json", (target / OWNER).read_bytes())
    common = {
        "target": target_record(target, owner),
        "anchor_head": HEAD,
        "logical_base_id": "b" * 64,
        "policy_ref": cc.make_ref("path-policy.json", raw),
        "environment_ref": cc.make_ref("environment.json", b"{}"),
        "payload_refs": refs,
    }
    before = before_manifest(view, foreign_dirty=["src/calc.py"], **common)
    assert before["source_sha256"] == source_identity(view)
    assert before["foreign_dirty_paths"] == ["src/calc.py"]
    assert before["target"]["filesystem_identity"]["inode"] == os.stat(target).st_ino
    assert [link["path"] for link in before["baseline_links"]] == ["calc-link"]
    with pytest.raises(CicdV2Error):
        before_manifest(view, foreign_dirty=["src/calc.py", "src/calc.py"], **common)


def _h(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _result(**changes: object) -> dict[str, object]:
    record = {
        "format": "millrace-plus-cicd-candidate-result/v1",
        "request_id": "req-1",
        "request_sha256": _h("request"),
        "operation": "import",
        "candidate_id": _h("candidate"),
        "materialization_id": "lease-1",
        "materialization_version": 3,
        "status": "completed",
        "target_before_sha256": _h("before"),
        "target_after_sha256": _h("after"),
        "artifact_refs": [{"path": "receipts/import.json", "sha256": _h("r")}],
        "writer_facts": {"core_invocation_id": "inv-9", "writer_state": "stopped"},
        "refusal": None,
    }
    return record | changes


def test_candidate_result_never_completes_a_write_with_unknown_writer() -> None:
    from millrace_plus.cicd_v2_records import CicdV2Error, validate_candidate_result

    assert validate_candidate_result(_result())["status"] == "completed"
    unknown = {"core_invocation_id": "inv-9", "writer_state": "unknown"}
    for operation in ("prepare-base", "import", "check", "restore"):
        with pytest.raises(CicdV2Error) as refused:
            validate_candidate_result(
                _result(operation=operation, writer_facts=unknown)
            )
        assert refused.value.code == "writer_unknown"
    # An unknown writer is retained honestly as held/uncertain, never completed.
    validate_candidate_result(_result(status="uncertain", writer_facts=unknown))
    # Verify is read-only toward its target: before and after must be equal.
    with pytest.raises(CicdV2Error):
        validate_candidate_result(_result(operation="verify"))
    validate_candidate_result(
        _result(
            operation="verify",
            target_after_sha256=_h("before"),
        )
    )
    for bad in (
        {"refusal": "x"},
        {"status": "refused"},
        {"materialization_version": True},
        {"extra": 1},
    ):
        with pytest.raises(CicdV2Error):
            validate_candidate_result(_result(**bad))


def test_candidate_verification_requires_complete_current_evidence() -> None:
    from millrace_plus.cicd_v2_records import (
        CicdV2Error,
        validate_candidate_verification,
    )

    record = {
        "format": "millrace-plus-cicd-candidate-verification/v1",
        "candidate_id": _h("candidate"),
        "materialization_id": "lease-1",
        "config_sha256": _h("config"),
        "observed_head": "c" * 40,
        "observed_source_sha256": _h("source"),
        "checks_refs": [{"path": "checks/a.json", "sha256": _h("a")}],
        "review_refs": [{"path": "reviews/1.json", "sha256": _h("v")}],
        "status": "verified",
        "evidence_refs": [],
        "refusal": None,
    }
    validate_candidate_verification(dict(record))
    for change in (
        {"checks_refs": []},
        {"review_refs": []},
        {"observed_head": None},
        {"refusal": "stale"},
    ):
        with pytest.raises(CicdV2Error):
            validate_candidate_verification(record | change)
    # A stale or refused verdict must say why.
    with pytest.raises(CicdV2Error):
        validate_candidate_verification(record | {"status": "stale"})
    validate_candidate_verification(record | {"status": "stale", "refusal": "head"})


def test_active_role_pointer_names_exact_prepared_attempt() -> None:
    from millrace_plus.cicd_v2_records import CicdV2Error, validate_active_role

    pointer = {
        "format": "millrace-plus-cicd-active-role/v2",
        "subject_id": "i" * 64,
        "role": "repair",
        "attempt_id": "attempt-3",
        "input_ref": {"path": "roles/input.json", "sha256": _h("in")},
        "projection_ref": {"path": "roles/projection.json", "sha256": _h("p")},
        "basis_sha256": _h("basis"),
    }
    for role in ("investigate", "repair", "review"):
        validate_active_role(pointer | {"role": role})
    for change in (
        {"role": "checks"},
        {"projection_ref": None},
        {"basis_sha256": "x"},
        {"extra": True},
    ):
        with pytest.raises(CicdV2Error):
            validate_active_role(pointer | change)


def _workarea(name: str, inode: int) -> dict[str, object]:
    return {
        "root": f"/srv/{name}",
        "filesystem_identity": {"path": f"/srv/{name}", "device": 1, "inode": inode},
        "owner_ref": {"path": f"owners/{name}.json", "sha256": _h(name)},
    }


def test_export_basis_prior_candidate_is_null_only_for_anchor_bootstrap() -> None:
    from millrace_plus.cicd_v2_records import CicdV2Error, validate_export_basis

    def ref(name: str) -> dict[str, object]:
        return {"path": f"{name}.json", "sha256": _h(name)}

    basis = {
        "format": "millrace-plus-cicd-export-basis/v1",
        "subject_ref": ref("subject"),
        "source_target": _workarea("dev", 10),
        "verification_target": _workarea("ci", 11),
        "anchor_head": "a" * 40,
        "logical_base_id": _h("base"),
        "prior_candidate_ref": ref("prior"),
        "before_manifest_ref": ref("before"),
        "source_after_manifest_ref": ref("after"),
        "config_ref": ref("config"),
        "assessment_ref": ref("assessment"),
        "authority_ref": ref("authority"),
        "writer_facts": {"core_invocation_id": "inv-1", "writer_state": "stopped"},
    }
    validate_export_basis(dict(basis))
    validate_export_basis(basis | {"prior_candidate_ref": None, "assessment_ref": None})
    # SPEC 7.5 line 357: a non-anchor export (assessed) needs its exact predecessor.
    with pytest.raises(CicdV2Error):
        validate_export_basis(basis | {"prior_candidate_ref": None})
    with pytest.raises(CicdV2Error):
        validate_export_basis(basis | {"assessment_ref": None})
    with pytest.raises(CicdV2Error) as live:
        validate_export_basis(
            basis
            | {
                "writer_facts": {
                    "core_invocation_id": "inv-1",
                    "writer_state": "may_live",
                }
            }
        )
    assert live.value.code == "writer_not_stopped"
    # Development and validation workareas must be distinct.
    with pytest.raises(CicdV2Error):
        validate_export_basis(basis | {"verification_target": _workarea("dev", 10)})


def test_repair_role_basis_needs_assessment_scope_and_reproduction_or_criteria() -> (
    None
):
    from millrace_plus.cicd_v2_records import CicdV2Error, validate_role_basis

    def ref(name: str) -> dict[str, object]:
        return {"path": f"{name}.json", "sha256": _h(name)}

    basis = {
        "format": "millrace-plus-cicd-role-basis/v1",
        "subject_schema_ref": ref("s"),
        "source_binding_ref": ref("binding"),
        "source_before_sha256": _h("src"),
        "config_sha256": _h("cfg"),
        "assessment_ref": ref("assessment"),
        "reproduction_refs": [ref("repro")],
        "criteria_ref": None,
        "scope_ref": ref("scope"),
        "public_writer_refs": [],
    }
    validate_role_basis(dict(basis), role="repair")
    validate_role_basis(
        basis | {"reproduction_refs": [], "criteria_ref": ref("c")}, role="repair"
    )
    # Investigation may start before any assessment exists.
    validate_role_basis(
        basis | {"assessment_ref": None, "scope_ref": None}, role="investigate"
    )
    for change in (
        {"assessment_ref": None},
        {"scope_ref": None},
        {"reproduction_refs": []},
    ):
        with pytest.raises(CicdV2Error) as refused:
            validate_role_basis(basis | change, role="repair")
        assert refused.value.code == "repair_basis_incomplete"


def test_factory_task_criteria_exception_never_weakens_failure_subjects():
    from types import SimpleNamespace

    from test_cicd_v2_records import _assessment
    from test_factory_records import task

    from millrace_plus import cicd_candidates as facade
    from millrace_plus.cicd_v2_records import CicdV2Error

    subject_ref = {"path": "factory-input.json", "sha256": "a" * 64}
    subject = task()
    home = SimpleNamespace(
        read_ref=lambda ref: subject, config={"source_policy": {"mode": "factory"}}
    )
    assert facade._role_subject(home, subject_ref) == subject
    assessment = _assessment() | {"subject_ref": subject_ref, "reproduction_refs": []}
    assert facade._assessment(home, assessment, subject_ref) == assessment
    for kind in ("ci_event", "user_report", "production_observation"):
        subject = task() | {
            "source_kind": kind,
            "request": None,
            "criteria": [],
            "feedback_ref": {"path": "original.json", "sha256": "b" * 64},
        }
        with pytest.raises(CicdV2Error, match="unsupported_repair"):
            facade._assessment(home, assessment, subject_ref)
    subject = {"format": "millrace-plus-cicd-input/v2"}
    with pytest.raises(CicdV2Error, match="unsupported_repair"):
        facade._assessment(home, assessment, subject_ref)


def test_factory_source_mode_needs_real_schema_and_selected_owner_not_a_marker():
    from types import SimpleNamespace

    from millrace_plus import cicd_candidates as facade
    from millrace_plus import factory_records as fr
    from millrace_plus.factory_candidate import _factory_consumer

    ref = {"path": "subject.json", "sha256": "a" * 64}
    home = SimpleNamespace(
        read_ref=lambda ref: {"format": fr.INPUT},
        config={"source_policy": {"mode": "factory"}},
        authority={"actors": []},
    )
    with pytest.raises(fr.FactoryError):
        facade._role_subject(home, ref)
    with pytest.raises(fr.FactoryError, match="factory_subject_owner_unavailable"):
        _factory_consumer(home, ref)
