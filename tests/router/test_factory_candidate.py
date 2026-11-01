"""Exact transfer rejects foreign state and preserves raw request obligations."""

from __future__ import annotations

from test_factory_records import task

from millrace_plus.factory_candidate import render_request


def test_rendered_request_keeps_original_ids_and_all_active_obligations():
    obligations = {
        "requirements": [
            {
                "id": "prior:sum",
                "text": "2+3 returns 5",
                "status": "active",
                "evidence_refs": [{"path": "accepted.json", "sha256": "a" * 64}],
            }
        ]
    }
    raw = render_request(task(), obligations)
    assert (
        b"Implement addition" in raw and b"prior:sum" in raw and b"accepted.json" in raw
    )
    obligations["requirements"][0]["text"] = "x" * 65536
    assert len(render_request(task(), obligations)) > 65536
    assert b"x" * 65536 in render_request(task(), obligations)


def test_lad_render_overflow_retains_complete_bytes_before_any_task_or_resume(
    monkeypatch,
):
    import hashlib
    import time
    from types import SimpleNamespace

    from millrace_plus import factory_lad
    from millrace_plus._factory_steps import Runtime

    subject = task() | {"request": "x" * 65536}
    objects = {"input": subject, "obligations": {"requirements": []}}
    retained = []
    runtime = object.__new__(Runtime)
    runtime.deadline = time.monotonic() + 30
    runtime._allowed = lambda operation: None
    runtime._save = lambda state, facts, phase: state
    runtime._transition = lambda state, facts, outcome, summary: (
        facts,
        outcome,
        summary,
    )

    def retain(raw, suffix):
        retained.append(raw)
        return {"path": "render.md", "sha256": hashlib.sha256(raw).hexdigest()}

    runtime.home = SimpleNamespace(
        read_ref=lambda ref: objects[ref], retain_bytes=retain
    )
    monkeypatch.setattr(
        factory_lad,
        "Lane",
        lambda home: SimpleNamespace(parked=lambda *a, **k: {"closed": True}),
    )
    facts, outcome, reason = runtime._submit_lad(
        {"input_ref": "input"}, {"obligations_ref": "obligations"}
    )
    assert outcome == "HOLD" and reason == "lad_request_too_large"
    assert retained == [render_request(subject, objects["obligations"])]
    assert len(retained[0]) > 65536 and facts["lad_rendered_ref"]["sha256"]


def test_actual_lad_git_read_preserves_raw_index_and_logical_facts(
    tmp_path, monkeypatch
):
    import hashlib
    import os
    import runpy
    import shutil
    import subprocess
    import sys

    from cicd_support import make_target

    from millrace_plus import catalog

    original, _ = make_target(tmp_path / "original")
    target = tmp_path / "read-copy"
    shutil.copytree(original, target)
    index = target / ".git/index"
    before = index.read_bytes()
    command = [
        "git",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.hooksPath=" + os.devnull,
        "-C",
        str(target),
    ]
    environment = {
        "PATH": os.defpath,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_OPTIONAL_LOCKS": "0",
    }
    args = ["diff", "--name-only", "-z", "--no-ext-diff", "--no-textconv"]
    old = subprocess.run(
        command + args, env=environment, capture_output=True, check=True
    )
    assert index.read_bytes() != before  # Demonstrated mandatory stat refresh.
    index.write_bytes(before)
    scripts = catalog.bundled_root() / "lad/scripts"
    monkeypatch.syspath_prepend(str(scripts))
    helper = runpy.run_path(str(scripts / "lad_state.py"))["_git_facts"]
    observed = helper(target, None)
    assert index.read_bytes() == before
    # A stale copied stat cache may conservatively name unchanged paths when
    # refresh is disabled. Actual source bytes and staged entries remain exact.
    corrected = subprocess.run(
        command[:1] + ["-c", "diff.autoRefreshIndex=false"] + command[1:] + args,
        env=environment,
        capture_output=True,
        check=True,
    )
    assert observed["worktree"] == hashlib.sha256(corrected.stdout).hexdigest()
    assert all(
        (target / p.relative_to(original)).read_bytes() == p.read_bytes()
        for p in original.rglob("*")
        if p.is_file() and ".git" not in p.parts
    )
    assert old.returncode == corrected.returncode == 0
    staged = subprocess.run(
        command + ["ls-files", "--stage", "-z"],
        env=environment,
        capture_output=True,
        check=True,
    ).stdout
    assert observed["index"] == hashlib.sha256(staged).hexdigest()
    del sys


def test_factory_metadata_refuses_parent_links_and_hardlinked_temporary_files(tmp_path):
    import pytest

    from millrace_plus.cicd_records import CicdError
    from millrace_plus.factory_runtime import write_exact, write_replace

    owned, outside = tmp_path / "owned", tmp_path / "outside"
    owned.mkdir()
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_bytes(b"unrelated original bytes")
    (owned / "roles").symlink_to(outside, target_is_directory=True)
    with pytest.raises(CicdError):
        write_exact(owned / "roles/new.json", b"must not escape")
    assert not (outside / "new.json").exists()
    temporary = owned / ".state.json.tmp"
    temporary.hardlink_to(sentinel)
    with pytest.raises(CicdError):
        write_replace(owned / "state.json", b"must not overwrite another link")
    assert sentinel.read_bytes() == b"unrelated original bytes"
    assert not (owned / "state.json").exists()


def test_logical_index_allows_status_refresh_but_retains_index_intent(tmp_path):
    import os
    import subprocess

    from cicd_support import git, make_target

    from millrace_plus.factory_candidate import index_identity

    target, _ = make_target(tmp_path / "index")
    before = index_identity(target)
    index = target / ".git/index"
    raw_before = index.read_bytes()
    path = target / "src/calc.py"
    raw = path.read_bytes()
    replacement = path.with_suffix(".replacement")
    replacement.write_bytes(raw)
    replacement.replace(path)
    subprocess.run(
        ["git", "-C", str(target), "status", "--porcelain"],
        env=os.environ | {"GIT_OPTIONAL_LOCKS": "1"},
        capture_output=True,
        check=True,
    )
    assert index.read_bytes() != raw_before and path.read_bytes() == raw
    assert index_identity(target) == before
    for flag in ["assume-unchanged", "skip-worktree"]:
        git(target, "update-index", "--" + flag, "src/calc.py")
        assert index_identity(target) != before
        git(target, "update-index", "--no-" + flag, "src/calc.py")
        assert index_identity(target) == before
    git(target, "update-index", "--chmod=+x", "src/calc.py")
    assert index_identity(target) != before
    git(target, "update-index", "--chmod=-x", "src/calc.py")
    assert index_identity(target) == before
    path.write_bytes(raw + b"# actual staged change\n")
    git(target, "add", "src/calc.py")
    assert index_identity(target) != before
    git(
        target,
        "update-index",
        "--cacheinfo",
        "100644,"
        + git(target, "rev-parse", "HEAD:src/calc.py").strip()
        + ",src/calc.py",
    )
    assert index_identity(target) == before
    empty = target / "empty.py"
    empty.touch()
    git(target, "add", "-N", "empty.py")
    intent = index_identity(target)
    staged = git(target, "ls-files", "--stage")
    git(target, "add", "empty.py")
    assert git(target, "ls-files", "--stage") == staged
    assert index_identity(target) != intent
    stable = index_identity(target)
    git(target, "config", "core.abbrev", "5")
    git(target, "config", "color.ui", "always")
    git(target, "config", "diff.renames", "copies")
    assert index_identity(target) == stable


def test_logical_index_preserves_unmerged_stages(tmp_path):
    import os
    import subprocess

    from cicd_support import git, make_target

    from millrace_plus.factory_candidate import index_identity

    target, _ = make_target(tmp_path / "stages")
    before = index_identity(target)
    oid = git(target, "rev-parse", "HEAD:src/calc.py").strip()
    raw = (
        f"0 {'0' * len(oid)}\tsrc/calc.py\n"
        f"100644 {oid} 1\tsrc/calc.py\n"
        f"100644 {oid} 2\tsrc/calc.py\n"
    ).encode()
    subprocess.run(
        ["git", "-C", str(target), "update-index", "--index-info"],
        input=raw,
        env=os.environ,
        check=True,
    )
    assert index_identity(target) != before
