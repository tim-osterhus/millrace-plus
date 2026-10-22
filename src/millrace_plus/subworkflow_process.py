"""Owned detached public replay processes with bounded, continuously drained logs."""

from __future__ import annotations

import os
import selectors
import subprocess
import sys
import time
import uuid
from pathlib import Path

from .subworkflow_records import canonical, canonical_path

__all__ = ["ControlProcess", "alive"]
LIMIT = 65536


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class ControlProcess:
    """One replay sender/controller. Only this object can claim a physical join."""

    def __init__(
        self,
        envelope: dict[str, object],
        home: Path,
        logs: Path,
        operation: str,
        *,
        env: dict[str, str],
    ) -> None:
        logs.mkdir(parents=True, exist_ok=True)
        self.process: subprocess.Popen[bytes] | None = None
        self.operation = operation
        self.selector = selectors.DefaultSelector()
        self.streams = {}
        self.output: dict[str, bytearray] = {
            "stdout": bytearray(),
            "stderr": bytearray(),
        }
        self.overflow = False
        self.joined = False
        self.envelope, self.home, self.env = envelope, home, env
        identifier = uuid.uuid4().hex
        try:
            for name in self.output:
                path = logs / f"{operation}-{identifier}.{name}"
                canonical_path(str(path))
                self.streams[name] = path.open("xb")
                os.chmod(path, 0o600)
        except BaseException:
            self.detach()
            raise

    def start(self) -> None:
        """Spawn only after the caller has reserved durable controller ownership."""
        assert self.process is None
        self.process = subprocess.Popen(
            [
                str(Path(sys.executable).parent / "millrace-plus"),
                "router",
                "replay",
                "--envelope",
                canonical(self.envelope).decode(),
                "--state-home",
                str(self.home),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self.env,
            start_new_session=True,
        )
        for name in self.output:
            pipe = getattr(self.process, name)
            assert pipe is not None
            os.set_blocking(pipe.fileno(), False)
            self.selector.register(pipe, selectors.EVENT_READ, name)

    @property
    def pid(self) -> int:
        assert self.process is not None
        return self.process.pid

    def poll(self, timeout: float = 0) -> bool:
        """Drain both pipes even after a limit; return true only after join and EOF."""
        if self.joined:
            return True
        assert self.process is not None
        for key, _ in self.selector.select(timeout):
            name = key.data
            try:
                chunk = os.read(key.fileobj.fileno(), 8192)
            except BlockingIOError:
                continue
            if not chunk:
                self.selector.unregister(key.fileobj)
                key.fileobj.close()
                self.streams[name].close()
                continue
            left = LIMIT - len(self.output[name])
            kept = chunk[: max(0, left)]
            self.output[name].extend(kept)
            self.streams[name].write(kept)
            self.streams[name].flush()
            if len(chunk) > left:
                self.overflow = True
        if self.process.poll() is not None and not self.selector.get_map():
            self.process.wait()
            self.selector.close()
            self.joined = True
        return self.joined

    def finish(self, seconds: float) -> bool:
        deadline = time.monotonic() + max(0, seconds)
        while not self.poll() and time.monotonic() < deadline:
            self.poll(min(0.05, max(0, deadline - time.monotonic())))
        return self.joined

    def detach(self) -> None:
        """Close only local handles, retaining the independently owned controller."""
        for key in list((self.selector.get_map() or {}).values()):
            self.selector.unregister(key.fileobj)
            key.fileobj.close()
        self.selector.close()
        for stream in self.streams.values():
            stream.close()
        if self.process is not None:
            for name in self.output:
                pipe = getattr(self.process, name)
                if pipe is not None:
                    pipe.close()
