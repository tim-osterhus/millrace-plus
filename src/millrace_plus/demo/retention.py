"""Private demo ownership, durable exports, and conservative cleanup."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .contracts import canonical_bytes

__all__ = ["create_root", "write_private", "export_results", "cleanup_root"]


def write_private(path: Path, raw: bytes) -> None:
    """Atomically replace one owner file and fsync its containing directory."""
    temporary = path.with_name("." + path.name + "-" + uuid.uuid4().hex)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(descriptor)
        os.replace(temporary, path)
        _sync(path.parent)
    finally:
        os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _sync(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def create_root() -> tuple[Path, dict[str, Any]]:
    root = Path(tempfile.mkdtemp(prefix="millrace-demo-")).resolve()
    root.chmod(0o700)
    for name in (
        "os-config",
        "os-state",
        "router-state",
        "workspace",
        "logs",
        "exports",
    ):
        (root / name).mkdir(mode=0o700)
    info = root.stat()
    owner = {
        "format": "millrace-demo-owner/v1",
        "demo_id": uuid.uuid4().hex,
        "root": str(root),
        "root_device": info.st_dev,
        "root_inode": info.st_ino,
        "created_at": datetime.now(UTC).isoformat(),
        "child_observation": None,
    }
    write_private(root / "owner.json", canonical_bytes(owner))
    return root, owner


def export_results(
    parent: Path, files: dict[str, bytes]
) -> tuple[Path, dict[str, str]]:
    """Create a unique durable result; failures leave source files untouched."""
    parent = parent.expanduser().absolute()
    parent.mkdir(parents=True, exist_ok=True)
    result = parent / (
        "demo-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex
    )
    result.mkdir(mode=0o700)
    hashes = {}
    for name, raw in files.items():
        if Path(name).name != name:
            raise ValueError("invalid_export_name")
        path = result / name
        with path.open("xb") as stream:
            os.chmod(path, 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        hashes[name] = hashlib.sha256(raw).hexdigest()
        if hashlib.sha256(path.read_bytes()).hexdigest() != hashes[name]:
            raise OSError("export_verification_failed")
    _sync(result)
    _sync(parent)
    return result, hashes


def cleanup_root(
    root: Path,
    owner: dict[str, Any],
    *,
    writers_stopped: bool,
    child_exited: bool,
    workers_drained: bool,
    service_stopped: bool,
    exports_verified: bool,
) -> bool:
    """Delete only our unchanged root after independent closure/export proofs."""
    if not all(
        (
            writers_stopped,
            child_exited,
            workers_drained,
            service_stopped,
            exports_verified,
        )
    ):
        return False
    try:
        info = root.lstat()
        marker = root / "owner.json"
        descriptor = os.open(marker, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
                return False
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                raw = stream.read(16385)
        finally:
            os.close(descriptor)
        if (
            not stat.S_ISDIR(info.st_mode)
            or root.resolve() != root
            or str(root) != owner["root"]
            or info.st_dev != owner["root_device"]
            or info.st_ino != owner["root_inode"]
            or json.loads(raw) != owner
            or not shutil.rmtree.avoids_symlink_attacks
        ):
            return False
        shutil.rmtree(root)
        return True
    except (OSError, ValueError, KeyError):
        return False
