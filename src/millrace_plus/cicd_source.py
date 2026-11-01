"""Target ownership, read-only Git identity, source snapshots and repair scope.

The owner marker and lock reproduce the existing persistent-LAD on-disk protocol
(`millrace-plus-target-owner/v1`) so LAD and CI/CD exclude each other on the same
exact target root. It is cooperative exclusion, not a sandbox. Nothing here
checks out, resets, commits or otherwise mutates Git state.
"""

from __future__ import annotations

import base64
import difflib
import fcntl
import hashlib
import os
import stat
import subprocess
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import cast

from . import check_contracts as cc
from .cicd_records import (
    REVISION,
    SMALL_FILES,
    SMALL_LINES,
    CicdError,
    digest,
    protected,
    read_bounded,
    repairable,
)
from .local_intake import canonical, strict_json

__all__ = [
    "OWNER",
    "canonical_dir",
    "filesystem_identity",
    "overlap",
    "claim_target",
    "target_lock",
    "git_head",
    "snapshot",
    "copy_before",
    "changed",
    "scope",
    "write_exact",
    "write_replace",
    "snapshot_policy",
    "source_identity",
    "baseline_links",
    "foreign_dirty_paths",
]

OWNER = ".millrace-plus-target-owner.json"
OWNER_FORMAT = "millrace-plus-target-owner/v1"
# Check products that never count as source. Everything else is source.
CACHE_DIRS = frozenset(
    {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
)
MAX_ENTRIES = 20000
MAX_HASHED = 512 * 1024 * 1024
MAX_COPIED_FILES = 4000
MAX_COPIED = 64 * 1024 * 1024


def canonical_dir(value: object, code: str, *, directory: bool = True) -> Path:
    """Absolute, normalized, unaliased path with no symlink component."""
    if not isinstance(value, str | Path):
        raise CicdError(code)
    text = str(value)
    path = Path(text)
    if not path.is_absolute() or os.path.normpath(text) != text or "\0" in text:
        raise CicdError(code)
    if path != path.resolve():
        raise CicdError(code)
    for part in (path, *path.parents):
        if part.is_symlink():
            raise CicdError(code)
        # samefile catches case aliases that resolve() can retain on macOS.
        if part.exists() and part.parent != part:
            for entry in part.parent.iterdir():
                if (
                    entry.name != part.name
                    and entry.name.casefold() == part.name.casefold()
                    and entry.samefile(part)
                ):
                    raise CicdError(code)
    if directory and not path.is_dir():
        raise CicdError(code)
    return path


def filesystem_identity(path: Path) -> dict[str, object]:
    info = path.stat()
    return {"path": str(path), "device": info.st_dev, "inode": info.st_ino}


def overlap(first: Path, second: Path) -> bool:
    if first == second or first in second.parents or second in first.parents:
        return True
    for left, right in ((first, second), (second, first)):
        if right.exists():
            for ancestor in (left, *left.parents):
                if ancestor.exists() and ancestor.samefile(right):
                    return True
    return False


def _owner(home: Path, service_id: str) -> dict[str, object]:
    return {
        "format": OWNER_FORMAT,
        "workflow_kind": "cicd",
        "service_id": service_id,
        "service_home": str(home),
        "service_home_identity": filesystem_identity(home),
    }


def _owner_check(raw: bytes, home: Path, service_id: str) -> None:
    try:
        value = strict_json(raw)
    except Exception as exc:
        raise CicdError("target_owner_conflict") from exc
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "format",
            "workflow_kind",
            "service_id",
            "service_home",
            "service_home_identity",
        }
        or value != _owner(home, service_id)
    ):
        raise CicdError("target_owner_conflict")
    identity = cast(dict[str, object], value["service_home_identity"])
    if type(identity["device"]) is not int or type(identity["inode"]) is not int:
        raise CicdError("target_owner_conflict")


def _check_target(target: Path, identity: dict[str, object]) -> None:
    canonical_dir(str(target), "target_identity_changed")
    if filesystem_identity(target) != identity:
        raise CicdError("target_identity_changed")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def claim_target(
    target: Path, identity: dict[str, object], home: Path, service_id: str
) -> None:
    """Exclusively create the marker, or accept only this exact CI owner."""
    _check_target(target, identity)
    marker = target / OWNER
    try:
        descriptor = os.open(
            marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600
        )
    except FileExistsError:
        raw = read_bounded(marker, 1048576, "target_owner_conflict")
        _owner_check(raw, home, service_id)
        return
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(canonical(_owner(home, service_id)) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        # A failed or partial marker is retained for inspection, never replaced.
        _check_target(target, identity)
        _fsync_directory(target)
    finally:
        os.close(descriptor)


@contextmanager
def target_lock(
    target: Path, identity: dict[str, object], home: Path, service_id: str
) -> Iterator[None]:
    """Lock the exact owned marker inode briefly. It never proves writer closure."""
    _check_target(target, identity)
    marker = target / OWNER
    try:
        descriptor = os.open(marker, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError as exc:
        # A missing marker is unknown authority; never recreate it as recovery.
        raise CicdError("target_owner_missing") from exc
    except OSError as exc:
        raise CicdError("target_owner_conflict") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise CicdError("target_owner_conflict")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise CicdError("target_busy") from exc
        current = marker.stat(follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
            raise CicdError("target_owner_changed")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(1048577)
        if len(raw) > 1048576:
            raise CicdError("target_owner_conflict")
        _owner_check(raw, home, service_id)
        _check_target(target, identity)
        yield
    finally:
        os.close(descriptor)


def git_head(target: Path) -> str:
    """Read the exact Git root and HEAD without optional locks or hooks."""
    env = {
        "PATH": os.environ.get("PATH", os.defpath),
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "LC_ALL": "C",
    }
    if "HOME" in os.environ:
        env["HOME"] = os.environ["HOME"]
    argv = ["git", "-c", "core.fsmonitor=false", "-C", str(target)]
    try:
        result = subprocess.run(
            [*argv, "rev-parse", "--show-toplevel", "HEAD"],
            cwd=target,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CicdError("target_not_git") from exc
    if result.returncode != 0 or len(result.stdout) > 8192:
        raise CicdError("target_not_git")
    lines = result.stdout.decode("utf-8", "replace").splitlines()
    if len(lines) != 2 or lines[0] != str(target) or not REVISION.fullmatch(lines[1]):
        raise CicdError("target_not_git_root")
    return lines[1]


def _hash(path: Path, budget: list[int]) -> str:
    value = hashlib.sha256()
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            while chunk := stream.read(1048576):
                budget[0] += len(chunk)
                if budget[0] > MAX_HASHED:
                    raise CicdError("source_too_large")
                value.update(chunk)
    finally:
        os.close(descriptor)
    return value.hexdigest()


def snapshot(target: Path) -> dict[str, dict[str, object]]:
    """Map every source entry to its kind, mode and digest.

    Excludes the root `.git`, the exact root owner marker and cache directories.
    """
    entries: dict[str, dict[str, object]] = {}
    budget = [0]

    def visit(folder: Path, prefix: str) -> None:
        with os.scandir(folder) as iterator:
            children = sorted(iterator, key=lambda item: item.name)
        for child in children:
            name = prefix + child.name
            if prefix == "" and child.name in {".git", OWNER}:
                continue
            if len(entries) >= MAX_ENTRIES:
                raise CicdError("source_too_large")
            info = child.stat(follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                if child.name not in CACHE_DIRS:
                    visit(Path(child.path), name + "/")
            elif stat.S_ISLNK(info.st_mode):
                entries[name] = {"kind": "symlink", "target": os.readlink(child.path)}
            elif stat.S_ISREG(info.st_mode):
                entries[name] = {
                    "kind": "file",
                    "mode": stat.S_IMODE(info.st_mode),
                    "sha256": _hash(Path(child.path), budget),
                }
            else:
                entries[name] = {"kind": "special"}

    visit(target, "")
    return entries


def snapshot_policy(
    target: Path,
    policy: cc.PathPolicy,
    guards: Mapping[str, int | float] | None = None,
) -> dict[str, object]:
    """Classified v2 snapshot under one captured shared path policy.

    `source` keeps every regular file's mode and digest; `links` keeps opaque
    lstat mode and raw `readlink` bytes (base64) with their digest, never
    followed. Environment paths are opaque: only their kind is listed and they
    are not traversed. `products` lists regular files and directories under
    product rules; any other product shape is listed in `unsupported`. Nested
    Git entries (`gitlinks`) and special files are listed so callers hold;
    nothing is changed. `guards` holds the operator-selected
    `resource_policy` values for `snapshot.entries` and `snapshot.bytes`; an
    omitted surface has no guard. Unlike v1 `snapshot`, no limit is built in.
    """
    guards = guards or {}
    entry_limit = guards.get("snapshot.entries")
    byte_limit = guards.get("snapshot.bytes")
    source: dict[str, dict[str, object]] = {}
    links: dict[str, dict[str, object]] = {}
    products: dict[str, dict[str, object]] = {}
    environment: dict[str, str] = {}
    gitlinks: list[str] = []
    special: list[str] = []
    unsupported: list[str] = []
    seen, hashed = [0], [0]

    def digest_file(path: Path) -> str:
        value = hashlib.sha256()
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                while chunk := stream.read(1048576):
                    hashed[0] += len(chunk)
                    if byte_limit is not None and hashed[0] > byte_limit:
                        raise CicdError("snapshot_guard_exceeded")
                    value.update(chunk)
        finally:
            os.close(descriptor)
        return value.hexdigest()

    def visit(folder: Path, prefix: str) -> None:
        with os.scandir(folder) as iterator:
            children = sorted(iterator, key=lambda item: item.name)
        for child in children:
            name = prefix + child.name
            if prefix == "" and child.name in {".git", OWNER}:
                continue
            seen[0] += 1
            if entry_limit is not None and seen[0] > entry_limit:
                raise CicdError("snapshot_guard_exceeded")
            info = child.stat(follow_symlinks=False)
            if child.name == ".git":
                gitlinks.append(prefix.rstrip("/"))
                continue
            # Every path is classified, so a selector under a product rule
            # still refuses `path_policy_conflict` rather than being inherited.
            kind = cc.classify(policy, name)
            if kind == "environment_paths":
                environment[name] = (
                    "directory"
                    if stat.S_ISDIR(info.st_mode)
                    else "symlink"
                    if stat.S_ISLNK(info.st_mode)
                    else "file"
                    if stat.S_ISREG(info.st_mode)
                    else "special"
                )
                continue
            if stat.S_ISDIR(info.st_mode):
                if kind == "product_paths":
                    products[name] = {"kind": "directory"}
                visit(Path(child.path), name + "/")
            elif stat.S_ISREG(info.st_mode):
                entry = {
                    "kind": "file",
                    "mode": stat.S_IMODE(info.st_mode),
                    "sha256": digest_file(Path(child.path)),
                }
                (products if kind == "product_paths" else source)[name] = entry
            elif kind == "product_paths":
                unsupported.append(name)
            elif stat.S_ISLNK(info.st_mode):
                raw = os.readlink(os.fsencode(child.path))
                links[name] = {
                    "mode": info.st_mode,
                    "target": base64.b64encode(raw).decode("ascii"),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
            else:
                special.append(name)

    visit(target, "")
    return {
        "source": source,
        "links": links,
        "products": products,
        "environment": environment,
        "gitlinks": sorted(gitlinks),
        "special": sorted(special),
        "unsupported": sorted(unsupported),
    }


def source_identity(view: dict[str, object]) -> str:
    """Candidate source digest: regular source files plus opaque baseline links.

    Product and environment entries never contribute, so a check's declared
    output cannot stale its own source eligibility.
    """
    return digest({"source": view["source"], "links": view["links"]})


def baseline_links(view: dict[str, object]) -> list[dict[str, object]]:
    """Sorted `{path,mode,target,sha256}` records for v2 tree/before manifests."""
    links = cast(dict[str, dict[str, object]], view["links"])
    return [{"path": name} | links[name] for name in sorted(links)]


def _git_read(
    target: Path, args: list[str], timeout_seconds: float, stdin: bytes | None = None
) -> bytes:
    """One read-only Git command with `git_head`'s lock/hook/prompt hardening."""
    env = {
        "PATH": os.environ.get("PATH", os.defpath),
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "LC_ALL": "C",
    }
    if "HOME" in os.environ:
        env["HOME"] = os.environ["HOME"]
    try:
        result = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-C", str(target), *args],
            cwd=target,
            env=env,
            input=stdin,
            stdin=None if stdin is not None else subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CicdError("git_inspection_failed") from exc
    if result.returncode != 0:
        raise CicdError("git_inspection_failed")
    return result.stdout


def foreign_dirty_paths(
    target: Path, head: str, view: dict[str, object], timeout_seconds: float
) -> list[str]:
    """Source paths whose task-start state differs from the frozen HEAD tree.

    This covers staged, unstaged and untracked edits, including ignored files,
    plus tracked files missing from the work tree. Contents compare by SHA-256
    of the exact HEAD blob bytes, file mode by Git's executable bit, and links by
    their raw target bytes. Environment/product paths and gitlinks are outside
    this classification. Only read-only Git plumbing runs. `timeout_seconds`
    is the caller's captured inspection guard (v1 `git_head` keeps its own
    existing 30-second guard; this helper adds none).
    """
    if not REVISION.fullmatch(head):
        raise CicdError("invalid_head")
    listing = _git_read(target, ["ls-tree", "-r", "-z", "--full-tree", head],
                        timeout_seconds)
    tracked: dict[str, tuple[str, str]] = {}
    for record in listing.split(b"\0"):
        if not record:
            continue
        meta, _, raw_path = record.partition(b"\t")
        mode, kind, oid = meta.decode("ascii").split(" ")
        if kind == "blob":
            tracked[os.fsdecode(raw_path)] = (mode, oid)
    blobs: dict[str, bytes] = {}
    if tracked:
        oids = sorted({oid for _, oid in tracked.values()})
        batch = _git_read(target, ["cat-file", "--batch"], timeout_seconds,
                          stdin="".join(f"{oid}\n" for oid in oids).encode("ascii"))
        offset = 0
        for oid in oids:
            end = batch.index(b"\n", offset)
            header = batch[offset:end].decode("ascii").split(" ")
            if header[0] != oid or header[1] != "blob":
                raise CicdError("git_inspection_failed")
            size = int(header[2])
            blobs[oid] = batch[end + 1:end + 1 + size]
            offset = end + 1 + size + 1
    source = cast(dict[str, dict[str, object]], view["source"])
    links = cast(dict[str, dict[str, object]], view["links"])
    outside = set(cast(dict[str, object], view["environment"])) | set(
        cast(dict[str, object], view["products"])
    )
    dirty: set[str] = set()
    for path in set(source) | set(links) | set(tracked):
        if any(path == other or path.startswith(other + "/") for other in outside):
            continue
        known = tracked.get(path)
        if path in source:
            entry = source[path]
            executable = bool(cast(int, entry["mode"]) & 0o111)
            if (
                known is None
                or known[0] not in {"100644", "100755"}
                or (known[0] == "100755") != executable
                or hashlib.sha256(blobs[known[1]]).hexdigest() != entry["sha256"]
            ):
                dirty.add(path)
        elif path in links:
            raw = base64.b64decode(cast(str, links[path]["target"]))
            if known is None or known[0] != "120000" or blobs[known[1]] != raw:
                dirty.add(path)
        else:
            dirty.add(path)  # Tracked at HEAD, absent now.
    return sorted(dirty)


def write_exact(
    path: Path, raw: bytes, code: str = "materialization_conflict"
) -> None:
    """Create one immutable owned file, or accept an exact earlier write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
    except FileExistsError:
        if read_bounded(path, len(raw), code) != raw:
            raise CicdError(code) from None
        return
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)
    _fsync_directory(path.parent)


def write_replace(path: Path, raw: bytes) -> None:
    """Atomically replace one mutable owned record."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    if temporary.is_symlink():
        raise CicdError("linked_record")
    with open(temporary, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    _fsync_directory(path.parent)


def copy_before(
    target: Path,
    before: dict[str, dict[str, object]],
    config: dict[str, object],
    destination: Path,
) -> None:
    """Retain exact before bytes for every repairable file; patches diff against it."""
    count = size = 0
    for name, entry in before.items():
        if entry["kind"] != "file" or not repairable(name, config):
            continue
        raw = read_bounded(target / name, MAX_COPIED, "source_changed")
        count, size = count + 1, size + len(raw)
        if count > MAX_COPIED_FILES or size > MAX_COPIED:
            raise CicdError("source_too_large")
        if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            raise CicdError("source_changed")
        write_exact(destination / name, raw, "freeze_replay_conflict")


def changed(
    before: dict[str, dict[str, object]], after: dict[str, dict[str, object]]
) -> list[str]:
    return sorted(
        name for name in set(before) | set(after) if before.get(name) != after.get(name)
    )


def _text(raw: bytes) -> list[str] | None:
    if b"\0" in raw:
        return None
    try:
        return raw.decode("utf-8").splitlines(keepends=True)
    except UnicodeError:
        return None


def scope(
    config: dict[str, object],
    allowed: list[str],
    before: dict[str, dict[str, object]],
    after: dict[str, dict[str, object]],
    before_root: Path,
    target: Path,
) -> dict[str, object]:
    """Apply the small-repair shape to actual differences, not worker claims.

    Returns `reason` None only for an acceptable patch. `patch` is produced when
    every difference is a text edit of a regular file, so held evidence remains.
    """
    paths = changed(before, after)
    reason: str | None = None
    if not paths:
        reason = "no_change"
    elif len(paths) > SMALL_FILES:
        reason = "too_many_files"
    sections: list[str] = []
    lines = 0
    diffable = True
    for name in paths:
        old, new = before.get(name), after.get(name)
        refusal = None
        if protected(name, config):
            refusal = "protected_path"
        elif not repairable(name, config):
            refusal = "outside_repair_paths"
        elif name not in allowed:
            refusal = "outside_proposed_paths"
        if refusal is not None:
            # No retained before bytes exist outside repair paths; list it only.
            reason = reason or refusal
            diffable = False
            continue
        if new is None:
            reason = reason or "file_deleted"
            diffable = False
            continue
        if new["kind"] != "file" or (old is not None and old["kind"] != "file"):
            reason = reason or "not_regular_file"
            diffable = False
            continue
        mode = cast(int, new["mode"])
        if (old is not None and old["mode"] != mode) or (
            old is None and mode & 0o111
        ):
            reason = reason or "mode_changed"
        old_raw = b"" if old is None else (before_root / name).read_bytes()
        new_raw = (target / name).read_bytes()
        old_digest = None if old is None else old["sha256"]
        if (old is not None and hashlib.sha256(old_raw).hexdigest() != old_digest) or (
            hashlib.sha256(new_raw).hexdigest() != new["sha256"]
        ):
            reason = reason or "source_changed"
            diffable = False
            continue
        old_lines, new_lines = _text(old_raw), _text(new_raw)
        if old_lines is None or new_lines is None:
            reason = reason or "binary_change"
            diffable = False
            continue
        section = [f"diff --git a/{name} b/{name}\n"]
        if old is None:
            section.append("new file mode 100644\n")
        body = difflib.unified_diff(
            old_lines,
            new_lines,
            "/dev/null" if old is None else f"a/{name}",
            f"b/{name}",
        )
        for index, line in enumerate(body):
            # The first two lines are the ---/+++ file headers.
            if index > 1 and line[:1] in {"+", "-"}:
                lines += 1
            if not line.endswith("\n"):
                line += "\n\\ No newline at end of file\n"
            section.append(line)
        sections.extend(section)
    if lines > SMALL_LINES:
        reason = reason or "too_many_lines"
    patch = "".join(sections).encode("utf-8") if diffable and paths else None
    return {
        "reason": reason,
        "changed_paths": paths,
        "lines": lines,
        "patch": patch,
        "source_after_sha256": digest(after),
    }
