"""CI/CD target ownership, exact Git identity, source drift and repair scope.

The cooperative owner marker and nonblocking lock are proven on the same exact
target root that persistent LAD uses. Nothing here starts a native worker, runs
a configured check, contacts a remote or mutates Git state.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest
from cicd_support import DEFECT, FIXED, GIT_ENV, config, make_target

from millrace_plus import cicd_source, lad_service
from millrace_plus.cicd_records import CicdError, digest
from millrace_plus.local_intake import IntakeError

GIT_PROBE = {**GIT_ENV, "GIT_OPTIONAL_LOCKS": "0"}
REPLAY_CASES = [
    "truncated",
    "extra-key",
    "duplicate-key",
    "wrong-kind",
    "wrong-home",
    "home-inode-changed",
    "symlink",
    "hardlink",
    "invalid-utf8",
    "oversized",
]


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _numbered(path: Path, count: int) -> None:
    _write(path, "".join(f"line {index}\n" for index in range(count)))


def _status(target: Path) -> bytes:
    """Porcelain status with optional index locks disabled."""
    child = subprocess.run(
        [
            "git",
            "-C",
            str(target),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ],
        env=GIT_PROBE,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert child.returncode == 0, child.stderr
    return child.stdout


def _root_owner(root: Path) -> tuple[Path, dict[str, object], Path, str]:
    """A plain user target claimed by one exact CI owner."""
    target = root / "target"
    target.mkdir(parents=True)
    (target / "notes.txt").write_text("user-owned notes\n")
    home = root / "home"
    home.mkdir()
    identity = cicd_source.filesystem_identity(target)
    service_id = uuid.uuid4().hex
    assert cicd_source.claim_target(target, identity, home, service_id) is None
    return target, identity, home, service_id


def _lad_config(root: Path, target: Path) -> dict[str, object]:
    """Frozen LAD service fields; only the service home must exist on disk."""
    home = root / "lad-home"
    home.mkdir(exist_ok=True)
    info = target.stat()
    return {
        "format": "millrace-plus-lad-service/v1",
        "service_id": uuid.uuid4().hex,
        "service_home": str(home),
        "target_root": str(target),
        "target_identity": {
            "path": str(target),
            "device": info.st_dev,
            "inode": info.st_ino,
        },
        "os_home": "local",
        "os_profile": {"path": str(root / "profile.json"), "sha256": "a" * 64},
        "worker_definition": {
            "selector": "bundled:lad",
            "version": "0.23.2",
            "sha256": "b" * 64,
        },
        "settings_sha256": "c" * 64,
    }


def _state(path: Path) -> object:
    """Exact marker bytes, or the symlink text that replaced them."""
    if path.is_symlink():
        return ("symlink", os.readlink(path))
    return ("bytes", path.read_bytes())


def _corrupt(case: str, marker: Path, valid: bytes, root: Path) -> None:
    value = json.loads(valid)
    if case == "truncated":
        marker.write_bytes(valid[: len(valid) // 2])
    elif case == "extra-key":
        marker.write_bytes(json.dumps({**value, "extra": True}).encode())
    elif case == "duplicate-key":
        marker.write_bytes(
            b'{"format":"millrace-plus-target-owner/v1",'
            b'"format":"millrace-plus-target-owner/v1"}'
        )
    elif case == "wrong-kind":
        marker.write_bytes(json.dumps({**value, "workflow_kind": "lad"}).encode())
    elif case == "wrong-home":
        elsewhere = str(root / "elsewhere")
        marker.write_bytes(json.dumps({**value, "service_home": elsewhere}).encode())
    elif case == "home-inode-changed":
        identity = dict(value["service_home_identity"])
        identity["inode"] += 1
        marker.write_bytes(
            json.dumps({**value, "service_home_identity": identity}).encode()
        )
    elif case == "symlink":
        marker.unlink()
        outside = root / "outside-owner.json"
        outside.write_bytes(valid)
        marker.symlink_to(outside)
    elif case == "hardlink":
        os.link(marker, root / "alias-owner.json")
    elif case == "invalid-utf8":
        marker.write_bytes(b"\xff" + valid)
    elif case == "oversized":
        marker.write_bytes(b" " * (1048576 + 1))
    else:
        raise AssertionError(case)


def test_lad_and_cicd_exclude_each_other(tmp_path: Path) -> None:
    root = tmp_path / "cicd-first"
    target, identity, home, service_id = _root_owner(root)
    marker = target / cicd_source.OWNER
    owned = marker.read_bytes()

    # CI owns the target first; the LAD claim and lock both refuse it.
    lad = _lad_config(root, target)
    lad_home = Path(str(lad["service_home"]))
    with pytest.raises(IntakeError) as claimed:
        lad_service.claim_target(lad, protected_roots=(lad_home,))
    assert claimed.value.code == "target_owner_conflict"
    with pytest.raises(IntakeError) as locked:
        with lad_service.target_lock(lad, protected_roots=(lad_home,)):
            pass
    assert locked.value.code == "target_owner_conflict"

    # The first CI owner also refuses a different home or service identity.
    other_home = root / "other-home"
    other_home.mkdir()
    with pytest.raises(CicdError) as other:
        cicd_source.claim_target(target, identity, other_home, service_id)
    assert other.value.code == "target_owner_conflict"
    with pytest.raises(CicdError) as renamed:
        cicd_source.claim_target(target, identity, home, uuid.uuid4().hex)
    assert renamed.value.code == "target_owner_conflict"
    assert marker.read_bytes() == owned

    # Reverse order: LAD owns a fresh target; CI cannot claim or lock it.
    lad_root = tmp_path / "lad-first"
    lad_target = lad_root / "target"
    lad_target.mkdir(parents=True)
    lad_owned = _lad_config(lad_root, lad_target)
    lad_owned_home = Path(str(lad_owned["service_home"]))
    lad_service.claim_target(lad_owned, protected_roots=(lad_owned_home,))
    lad_marker = lad_target / cicd_source.OWNER
    lad_bytes = lad_marker.read_bytes()
    lad_identity = cicd_source.filesystem_identity(lad_target)
    with pytest.raises(CicdError) as ci_claim:
        cicd_source.claim_target(lad_target, lad_identity, home, service_id)
    assert ci_claim.value.code == "target_owner_conflict"
    with pytest.raises(CicdError) as ci_lock:
        with cicd_source.target_lock(lad_target, lad_identity, home, service_id):
            pass
    assert ci_lock.value.code == "target_owner_conflict"
    assert lad_marker.read_bytes() == lad_bytes


@pytest.mark.parametrize("case", REPLAY_CASES)
def test_owner_replay_and_corruption(tmp_path: Path, case: str) -> None:
    target, identity, home, service_id = _root_owner(tmp_path)
    marker = target / cicd_source.OWNER
    # Exact replay is accepted. A semantically equal reformat is accepted too,
    # because owner equality is parsed-object equality.
    assert cicd_source.claim_target(target, identity, home, service_id) is None
    value = json.loads(marker.read_bytes())
    marker.write_bytes(json.dumps(value, indent=2).encode())
    assert cicd_source.claim_target(target, identity, home, service_id) is None
    with cicd_source.target_lock(target, identity, home, service_id):
        pass

    valid = marker.read_bytes()
    _corrupt(case, marker, valid, tmp_path)
    before = _state(marker)
    with pytest.raises(CicdError) as claim:
        cicd_source.claim_target(target, identity, home, service_id)
    assert claim.value.code == "target_owner_conflict"
    with pytest.raises(CicdError) as lock:
        with cicd_source.target_lock(target, identity, home, service_id):
            pass
    assert lock.value.code == "target_owner_conflict"
    assert _state(marker) == before


def test_owner_lock_and_path_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A second open file description on the same marker is busy, then released.
    target, identity, home, service_id = _root_owner(tmp_path / "lock")
    with cicd_source.target_lock(target, identity, home, service_id):
        with pytest.raises(CicdError) as busy:
            with cicd_source.target_lock(target, identity, home, service_id):
                pass
        assert busy.value.code == "target_busy"
    with cicd_source.target_lock(target, identity, home, service_id):
        pass

    # A recreated home has a new inode, so the marker's home identity mismatches.
    root = tmp_path / "home-case"
    target, identity, home, service_id = _root_owner(root)
    home.rename(root / "home-original")
    home.mkdir()
    with pytest.raises(CicdError) as home_changed:
        with cicd_source.target_lock(target, identity, home, service_id):
            pass
    assert home_changed.value.code == "target_owner_conflict"

    # A recreated target fails the exact root identity before the marker is read.
    root = tmp_path / "target-case"
    target, identity, home, service_id = _root_owner(root)
    target.rename(root / "target-original")
    target.mkdir()
    with pytest.raises(CicdError) as target_changed:
        with cicd_source.target_lock(target, identity, home, service_id):
            pass
    assert target_changed.value.code == "target_identity_changed"

    # A missing marker is unknown authority and is never recreated as recovery.
    root = tmp_path / "missing-case"
    target, identity, home, service_id = _root_owner(root)
    marker = target / cicd_source.OWNER
    marker.unlink()
    with pytest.raises(CicdError) as missing:
        with cicd_source.target_lock(target, identity, home, service_id):
            pass
    assert missing.value.code == "target_owner_missing"
    assert not marker.exists()

    # Replacing the marker after the fd is opened is caught by the inode recheck.
    root = tmp_path / "swap-case"
    target, identity, home, service_id = _root_owner(root)
    marker = target / cicd_source.OWNER
    owned = marker.read_bytes()
    real_flock = fcntl.flock

    def swap_after_lock(descriptor: int, operation: int) -> None:
        real_flock(descriptor, operation)
        os.replace(marker, root / "moved-owner.json")
        marker.write_bytes(owned)

    monkeypatch.setattr(fcntl, "flock", swap_after_lock)
    with pytest.raises(CicdError) as swapped:
        with cicd_source.target_lock(target, identity, home, service_id):
            pass
    assert swapped.value.code == "target_owner_changed"


@pytest.mark.parametrize(
    "form", ["relative", "dot-dot", "trailing-dot", "symlinked-parent"]
)
def test_canonical_dir_refuses_noncanonical_forms(tmp_path: Path, form: str) -> None:
    real = tmp_path / "real" / "target"
    real.mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "real", target_is_directory=True)
    assert cicd_source.canonical_dir(str(real), "noncanonical_target") == real
    value = {
        "relative": "relative/target",
        "dot-dot": str(real / ".." / "target"),
        "trailing-dot": f"{real}/.",
        "symlinked-parent": str(link / "target"),
    }[form]
    with pytest.raises(CicdError) as refused:
        cicd_source.canonical_dir(value, "noncanonical_target")
    assert refused.value.code == "noncanonical_target"


def test_revision_and_dirty_before_state(tmp_path: Path) -> None:
    target, head = make_target(tmp_path)
    assert cicd_source.git_head(target) == head
    index = (target / ".git" / "index").read_bytes()
    dirty = b"dirty calculator notes\n"
    (target / "src" / "notes.txt").write_bytes(dirty)
    status = _status(target)
    assert b" M src/notes.txt" in status
    assert cicd_source.git_head(target) == head
    assert (target / ".git" / "index").read_bytes() == index
    assert _status(target) == status

    # Only the exact Git root is accepted; a subdirectory and a plain folder are not.
    with pytest.raises(CicdError) as subdir:
        cicd_source.git_head(target / "src")
    assert subdir.value.code == "target_not_git_root"
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(CicdError) as plain_refused:
        cicd_source.git_head(plain)
    assert plain_refused.value.code == "target_not_git"

    # The dirty baseline becomes exact retained before bytes, never a clean copy.
    entries = cicd_source.snapshot(target)
    assert ".git" not in entries
    assert entries["src/notes.txt"]["sha256"] == hashlib.sha256(dirty).hexdigest()
    before_root = tmp_path / "before"
    cicd_source.copy_before(
        target, entries, config(target, tmp_path / "peer"), before_root
    )
    assert (before_root / "src" / "notes.txt").read_bytes() == dirty


def test_source_drift_holds(tmp_path: Path) -> None:
    target, _ = make_target(tmp_path)
    baseline = digest(cicd_source.snapshot(target))

    # Cache products at any depth, the root Git store and the exact root owner
    # marker are not source.
    _write(target / "src" / "__pycache__" / "calc.cpython-311.pyc", "cache")
    _write(target / "src" / "pkg" / "__pycache__" / "mod.pyc", "cache")
    _write(target / "src" / "pkg" / ".mypy_cache" / "cache.json", "{}\n")
    _write(target / "src" / ".ruff_cache" / "cache", "cache\n")
    _write(target / ".pytest_cache" / "README", "cache\n")
    _write(target / ".git" / "fixture-note", "git internal\n")
    _write(target / cicd_source.OWNER, "{}\n")
    assert digest(cicd_source.snapshot(target)) == baseline

    # A real source edit changes the digest. A nested owner-named file is source.
    _write(target / "src" / "calc.py", FIXED)
    edited = digest(cicd_source.snapshot(target))
    assert edited != baseline
    _write(target / "src" / cicd_source.OWNER, "{}\n")
    assert digest(cicd_source.snapshot(target)) != edited

    # Bytes changed after the snapshot are refused before any retained copy.
    before = cicd_source.snapshot(target)
    _write(target / "src" / "calc.py", DEFECT)
    with pytest.raises(CicdError) as drift:
        cicd_source.copy_before(
            target, before, config(target, tmp_path / "peer"), tmp_path / "before"
        )
    assert drift.value.code == "source_changed"


def _grow(target: Path, sizes: tuple[int, int]) -> None:
    """One counted edit to calc.py (two lines) plus two new files of given sizes."""
    (target / "src" / "calc.py").write_text(FIXED)
    _numbered(target / "src" / "a.py", sizes[0])
    _numbered(target / "src" / "b.py", sizes[1])


def _four_files(target: Path) -> None:
    _grow(target, (1, 1))
    _numbered(target / "src" / "c.py", 1)


def _scope_after(
    root: Path, edit: Callable[[Path], None], allowed: list[str]
) -> dict[str, object]:
    """Freeze before bytes, apply one owned edit, then run the repair scope."""
    target, _ = make_target(root)
    settings = config(target, root / "peer")
    before = cicd_source.snapshot(target)
    before_root = root / "before"
    cicd_source.copy_before(target, before, settings, before_root)
    edit(target)
    after = cicd_source.snapshot(target)
    return cicd_source.scope(settings, allowed, before, after, before_root, target)


def test_small_repair_shape_boundaries(tmp_path: Path) -> None:
    small = ["src/a.py", "src/b.py", "src/calc.py"]
    # calc.py contributes 2 counted lines; each new 99-line file contributes 99.
    passed = _scope_after(tmp_path / "pass", lambda t: _grow(t, (99, 99)), small)
    assert passed["reason"] is None
    assert passed["lines"] == 200
    assert passed["changed_paths"] == small
    patch = passed["patch"]
    assert isinstance(patch, bytes)
    assert patch.startswith(b"diff --git a/src/a.py b/src/a.py\n")
    assert b"notes.txt" not in patch

    four = _scope_after(tmp_path / "four", _four_files, [*small, "src/c.py"])
    assert four["reason"] == "too_many_files"

    # One more counted line than the boundary is refused.
    long = _scope_after(tmp_path / "long", lambda t: _grow(t, (100, 99)), small)
    assert long["reason"] == "too_many_lines"
    assert long["lines"] == 201


def _delete_notes(target: Path) -> None:
    (target / "src" / "notes.txt").unlink()


def _rename_notes(target: Path) -> None:
    os.rename(target / "src" / "notes.txt", target / "src" / "notes2.txt")


def _binary_calc(target: Path) -> None:
    (target / "src" / "calc.py").write_bytes(b"\x00\x01\x02binary")


def _chmod_calc(target: Path) -> None:
    os.chmod(target / "src" / "calc.py", 0o755)


def _symlink_notes(target: Path) -> None:
    (target / "src" / "notes.txt").unlink()
    os.symlink("calc.py", target / "src" / "notes.txt")


def _outside_readme(target: Path) -> None:
    _write(target / "README.md", "outside repair paths\n")


def _protected_pyproject(target: Path) -> None:
    with (target / "pyproject.toml").open("a") as stream:
        stream.write("# protected edit\n")


def _unproposed_notes(target: Path) -> None:
    _write(target / "src" / "notes.txt", "edited notes\n")


def _no_edit(target: Path) -> None:
    """No source change at all."""


FORBIDDEN: dict[str, tuple[Callable[[Path], None], list[str], str]] = {
    "delete": (_delete_notes, ["src/notes.txt"], "file_deleted"),
    "rename": (_rename_notes, ["src/notes.txt", "src/notes2.txt"], "file_deleted"),
    "binary": (_binary_calc, ["src/calc.py"], "binary_change"),
    "chmod": (_chmod_calc, ["src/calc.py"], "mode_changed"),
    "symlink": (_symlink_notes, ["src/notes.txt"], "not_regular_file"),
    "outside-repair": (_outside_readme, ["README.md"], "outside_repair_paths"),
    "protected": (_protected_pyproject, ["pyproject.toml"], "protected_path"),
    "unproposed": (_unproposed_notes, ["src/calc.py"], "outside_proposed_paths"),
    "no-change": (_no_edit, [], "no_change"),
}


@pytest.mark.parametrize("case", list(FORBIDDEN))
def test_forbidden_repair_shapes(tmp_path: Path, case: str) -> None:
    edit, allowed, reason = FORBIDDEN[case]
    result = _scope_after(tmp_path / case, edit, allowed)
    assert result["reason"] == reason


def test_owner_marker_is_protected_and_not_patch_content(tmp_path: Path) -> None:
    target, identity, home, service_id = _root_owner(tmp_path)
    marker = target / cicd_source.OWNER
    settings = config(target, tmp_path / "peer")
    before = cicd_source.snapshot(target)
    assert cicd_source.OWNER not in before

    # A semantic tamper of the exact root owner is authority, not repair content.
    value = json.loads(marker.read_bytes())
    value["workflow_kind"] = "lad"
    marker.write_bytes(json.dumps(value).encode())
    after = cicd_source.snapshot(target)
    result = cicd_source.scope(
        settings, [], before, after, tmp_path / "before", target
    )
    assert result["changed_paths"] == []
    assert result["reason"] == "no_change"
    with pytest.raises(CicdError) as conflict:
        with cicd_source.target_lock(target, identity, home, service_id):
            pass
    assert conflict.value.code == "target_owner_conflict"

    # A nested file with the owner name is not excluded and is protected.
    nested_before = cicd_source.snapshot(target)
    nested_name = f"src/{cicd_source.OWNER}"
    _write(target / nested_name, "{}\n")
    nested_after = cicd_source.snapshot(target)
    nested = cicd_source.scope(
        settings,
        [nested_name],
        nested_before,
        nested_after,
        tmp_path / "before",
        target,
    )
    assert nested["changed_paths"] == [nested_name]
    assert nested["reason"] == "protected_path"
