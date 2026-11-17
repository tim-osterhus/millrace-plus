"""Inert Forge binding-executable fake. No SDK, provider or network access.

Tests copy this file as the binding ``executable`` with a ``#!python`` line and
an options file at ``<executable>.json``. The adapter then runs it as

    EXECUTABLE -I -B -X utf8 <root>/forge-probe.py
    EXECUTABLE -I -B -X utf8 <root>/forge-worker.py <root>/forge-request.json

Probe and main calls emit scripted bytes and exit codes. With ``real_worker``
the main call instead runs the real packaged worker over the test-only fake
``forge`` package in ``forge_fake_sdk/``. That proves worker logic only, never
the real SDK. ``real_probe`` likewise runs the real probe script.

Fake injection mechanism (test-only): the fixed ``-I`` argv reaches this file,
not a Python interpreter, so ``-I`` isolation is not exercised here. The module
search path is left unchanged. Before running the worker with ``runpy``, this file
adds one exact-name finder to ``sys.meta_path``. It answers only ``forge`` and
``forge.errors``, building their specs from the owned files in
``forge_fake_sdk/forge/``. The fake package body therefore still runs only when
the worker imports ``forge``, after its runtime and request checks. It also wraps
``importlib.metadata.distribution`` so that the name ``forge-guardrails`` alone
resolves to the owned fake ``forge_guardrails-0.10.1.dist-info``. Every other name
goes to the original function. ``real_probe`` gets only the metadata wrapper and
never loads the fake package. None of this is production interpreter isolation,
environment identity (G03) or real-SDK proof (G05).

``sys.version_info`` is replaced only when the options name ``python``. Worker
cases are meant to run on the admitted real CPython 3.12 test interpreter; the
override exists only to simulate a version mismatch.
"""

import base64
import collections
import importlib.abc
import importlib.util
import json
import os
import re
import runpy
import signal  # noqa: F401 - available to test-owned exec hooks
import subprocess  # noqa: F401
import sys
import threading  # noqa: F401
import time  # noqa: F401
from importlib import metadata
from pathlib import Path

FAKE_SDK = Path(__file__).resolve().parent / "forge_fake_sdk"
FLAGS = ["-I", "-B", "-X", "utf8"]
VersionInfo = collections.namedtuple(
    "VersionInfo",
    "major minor micro releaselevel serial",
)


def _bytes(options, key, default=b""):
    return base64.b64decode(options[key]) if key in options else default


def _log(options, kind):
    if options.get("starts"):
        with Path(options["starts"]).open("a") as log:
            log.write(json.dumps([kind, *sys.argv[1:]]) + "\n")


class _FakeForgeFinder(importlib.abc.MetaPathFinder):
    """Exact-name finder for the owned fake package; specs only, no execution."""

    def __init__(self, package):
        self.package = package

    def find_spec(self, name, path=None, target=None):
        if name == "forge":
            return importlib.util.spec_from_file_location(
                "forge",
                self.package / "__init__.py",
                submodule_search_locations=[str(self.package)],
            )
        if name == "forge.errors":
            return importlib.util.spec_from_file_location(
                "forge.errors", self.package / "errors.py"
            )
        return None


def _fake_metadata(root):
    """Resolve only ``forge-guardrails`` to the owned fake dist-info."""
    fake = metadata.PathDistribution(root / "forge_guardrails-0.10.1.dist-info")
    original = metadata.distribution

    def distribution(name):
        if re.sub(r"[-_.]+", "-", name).lower() == "forge-guardrails":
            return fake
        return original(name)

    metadata.distribution = distribution


def _isolate(options, *, modules):
    """Install the fake metadata (and, for the worker, the fake package finder)."""
    # Tests copy this file elsewhere, so the support helper passes the fake root.
    root = Path(options.get("fake_sdk", FAKE_SDK))
    _fake_metadata(root)
    if modules:
        sys.meta_path.insert(0, _FakeForgeFinder(root / "forge"))
    if options.get("scenario"):
        os.environ["FORGE_FAKE_SCENARIO"] = options["scenario"]
    if "python" in options:  # Mismatch simulation only; never on the normal path.
        python = options["python"]
        sys.version_info = VersionInfo(python[0], python[1], python[2], "final", 0)


def _probe(options):
    _log(options, "probe")
    if options.get("real_probe"):
        _isolate(options, modules=False)
        script = sys.argv[5]
        sys.argv = [script]
        runpy.run_path(script, run_name="__main__")
        return 0
    exec(options.get("probe_before", ""))
    os.write(2, _bytes(options, "probe_stderr"))
    os.write(1, _bytes(options, "probe_stdout"))
    return options.get("probe_exit", 0)


def _main(options):
    _log(options, "main")
    if options.get("real_worker"):
        _isolate(options, modules=True)
        worker, request = sys.argv[5], sys.argv[6]
        sys.argv = [worker, request]
        runpy.run_path(worker, run_name="__main__")
        return 0
    stdin = sys.stdin.buffer.read()
    if options.get("capture"):
        Path(options["capture"]).write_text(
            json.dumps(
                {
                    "argv": sys.argv[1:],
                    "stdin": base64.b64encode(stdin).decode(),
                    "cwd": os.getcwd(),
                    "environment": dict(os.environ),
                }
            )
        )
    exec(options.get("before", ""))
    os.write(2, _bytes(options, "stderr"))
    wire = _bytes(options, "wire")
    if options.get("echo_invocation"):
        # Flow fakes learn the Core-assigned invocation id from the request.
        selected = json.loads(Path(sys.argv[6]).read_text())["invocation_id"]
        wire = wire.replace(b"__INVOCATION__", selected.encode())
    view = memoryview(wire)
    while view:
        view = view[os.write(1, view) :]
    exec(options.get("after", ""))
    return options.get("exit_code", 0)


def main():
    options = json.loads(Path(sys.argv[0] + ".json").read_text())
    if sys.argv[1:5] != FLAGS:
        return 64
    if len(sys.argv) == 6 and sys.argv[5].endswith("/forge-probe.py"):
        return _probe(options)
    if (
        len(sys.argv) == 7
        and sys.argv[5].endswith("/forge-worker.py")
        and sys.argv[6].endswith("/forge-request.json")
    ):
        return _main(options)
    return 64


if __name__ == "__main__":
    sys.exit(main())
