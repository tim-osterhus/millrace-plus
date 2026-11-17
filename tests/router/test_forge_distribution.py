"""Forge static and packaging cases. No case imports or runs the Forge SDK.

Module scope imports no Plus source package. Cases that need the source modules
import them inside the test. ``test_installed_resolver_and_assets`` uses only
artifacts and interpreters named by a verifier-supplied manifest and performs no
build or install. The source resolver selects the exact ``forge_runner`` pair; the
installed proof stays pending until a prebuilt artifact manifest is supplied.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[2]
ROUTER = PROJECT / "src" / "millrace_plus" / "router"
WORKER = ROUTER / "forge_worker.py"
PROBE = ROUTER / "forge_probe.py"
DOC = PROJECT / "docs" / "forge-runner-adapter.md"
STDLIB = set(sys.stdlib_module_names)


def imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_bytes(), str(path))):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(("." * node.level) + node.module)
    return found


def names(path: Path) -> set[str]:
    tree = ast.parse(path.read_bytes(), str(path))
    return (
        {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        | {
            k.arg
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            for k in n.keywords
            if k.arg
        }
    )


def test_scripts_parse_on_python_311():
    """C4: a real CPython 3.11 must parse both scripts; feature_version is a hint."""
    for path in (WORKER, PROBE):
        ast.parse(path.read_bytes(), str(path), feature_version=(3, 11))
    if sys.version_info[:2] == (3, 11):
        for path in (WORKER, PROBE):
            ast.parse(path.read_bytes(), str(path))
        return
    interpreter = shutil.which("python3.11")
    if interpreter is None:
        pytest.skip("no real CPython 3.11 available; the 3.11 parse proof is unrun")
    subprocess.run(
        [
            interpreter,
            "-I",
            "-B",
            "-c",
            "import ast,sys\nfor p in sys.argv[1:]: ast.parse(open(p,'rb').read(),p)",
            str(WORKER),
            str(PROBE),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )


def test_executable_is_binding_interpreter(tmp_path):
    from forge_support import request

    from millrace_plus.router import forge_configuration as c

    plan = c.plan(request(tmp_path, executable="/opt/forge-env/bin/python3.12"))
    assert plan.argv[0] == "/opt/forge-env/bin/python3.12"
    assert c.probe_argv(plan.argv[0], "/r")[0] == plan.argv[0]
    for path in (ROUTER / "forge.py", ROUTER / "forge_configuration.py"):
        assert "which" not in names(path)  # No interpreter discovery (W-RT-01).
        assert "sys.executable" not in path.read_text()
        assert "shutil" not in imports(path)


def test_worker_has_no_signal_or_cancel_hooks():
    assert "signal" not in imports(WORKER)
    found = names(WORKER)
    assert not found & {"cancel_event", "add_signal_handler", "cancel", "_exit"}


def test_worker_imports_and_forbidden_calls():
    used = imports(WORKER)
    assert {"forge", "forge.errors"} <= used
    for module in used - {"forge", "forge.errors", "__future__"}:
        assert module.split(".")[0] in STDLIB, module
    assert not {m.split(".")[0] for m in used} & {
        "millrace",
        "millrace_plus",
        "httpx",
        "pydantic",
        "subprocess",
        "socket",
        "signal",
        "threading",
        "multiprocessing",
    }
    assert not names(WORKER) & {
        "setup_backend",
        "ServerManager",
        "detect_hardware",
        "SlotWorker",
        "proxy",
        "rpc",
        "LlamafileClient",
        "AnthropicClient",
        "OllamaClient",
    }
    text = WORKER.read_text()
    assert "sqlite3" not in text and "millrace.router" not in text


def test_probe_source_static():
    used = imports(PROBE)
    assert all(m.split(".")[0] in STDLIB for m in used), used
    assert not any(m.split(".")[0] == "forge" for m in used)
    tree = ast.parse(PROBE.read_bytes())
    pinned = next(
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "PINNED" for t in node.targets)
    )
    entries = ast.literal_eval(pinned)
    assert len(entries) == 53 and len({name for name, _ in entries}) == 53
    assert all(name.startswith("forge/") for name, _ in entries)
    assert sum(name.endswith(".py") for name, _ in entries) == 52
    assert dict(entries)["forge/py.typed"] == hashlib.sha256(b"").hexdigest()
    assert dict(entries)["forge/core/runner.py"].startswith("0cbe2fc4")
    assert dict(entries)["forge/core/runner.py"].endswith("1c16")


def test_probe_runs_over_fake_distribution(tmp_path):
    """The real probe emits one grammar-valid line; the fake files never match."""
    from forge_support import prepared

    from millrace_plus.router import forge_configuration as c

    plan = prepared(tmp_path, real_probe=True)
    root = str(Path(plan.details["probe_path"]).parent)
    result = subprocess.run(
        list(c.probe_argv(plan.argv[0], root)),
        cwd=tmp_path,
        capture_output=True,
        timeout=60,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0 and result.stderr == b""
    report = c.parse_probe(result.stdout)
    assert report is not None
    assert report["forge_guardrails"] == "0.10.1" and report["files_match"] is False
    assert not c.probe_accepted(report)


def test_doc_states_read_tool_limits():
    text = DOC.read_text()
    for phrase in (
        "hard link",
        "mount point",
        "race window",
        "atime",
        "egress",
        ".env",
        "response size",
        "not a sandbox",
        "UNKNOWN",
        "not qualified",
    ):
        assert phrase in text, phrase


def test_sdk_not_a_plus_dependency():
    project = tomllib.loads((PROJECT / "pyproject.toml").read_text())["project"]
    declared = [
        *project["dependencies"],
        *(
            d
            for group in project.get("optional-dependencies", {}).values()
            for d in group
        ),
    ]
    assert not any("forge" in item.lower() for item in declared), declared
    for path in ROUTER.glob("*.py"):
        if path.name in {"forge_worker.py"}:
            continue
        assert not any(m.split(".")[0] == "forge" for m in imports(path)), path.name


MANIFEST_ENV = "FORGE_R2B_ARTIFACT_MANIFEST"
MANIFEST_FORMAT = "millrace-forge-r2b-artifacts/v1"
FORGE_MODULES = (
    "forge.py",
    "forge_configuration.py",
    "forge_worker.py",
    "forge_probe.py",
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def supplied_artifacts() -> dict:
    """Read the verifier-supplied manifest. A missing manifest leaves the case unrun.

    This never builds, installs or provisions anything. The verifier precreates
    the artifacts and both interpreters, then names the manifest in
    ``FORGE_R2B_ARTIFACT_MANIFEST``.
    """
    location = os.environ.get(MANIFEST_ENV)
    if not location:
        pytest.skip(
            f"unrun: no admitted artifact manifest named by {MANIFEST_ENV}; "
            "this case never builds or installs"
        )
    manifest = json.loads(Path(location).read_text())
    assert manifest["format"] == MANIFEST_FORMAT
    for key in ("plus_wheel", "plus_sdist", "core_wheel"):
        path = Path(manifest[key]["path"])
        assert path.is_absolute() and path.is_file(), key
        assert _sha(path) == manifest[key]["sha256"], key
    for key in ("installed_python", "core_only_python"):
        path = Path(manifest[key])
        assert path.is_absolute() and os.access(path, os.X_OK), key
    root = manifest.get("source_assets_root")
    assert root is None or Path(root).is_absolute()
    return manifest


def pinned_constants(source: bytes) -> dict[str, str]:
    """Read the two digest constants from module text without importing it."""
    found = {}
    for node in ast.parse(source).body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in {"WORKER_SHA256", "PROBE_SHA256"}
        ):
            found[node.targets[0].id] = ast.literal_eval(node.value)
    assert set(found) == {"WORKER_SHA256", "PROBE_SHA256"}
    return found


def child(python: str, code: str, cwd: Path) -> dict:
    """Run one isolated child interpreter and return its JSON origin receipt."""
    result = subprocess.run(
        [python, "-I", "-B", "-c", code],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=120,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.splitlines()[-1])


INERT = """
import json, sys
import millrace_plus.router
print(json.dumps({"millrace_loaded": "millrace" in sys.modules,
                  "forge_loaded": "forge" in sys.modules,
                  "plus_origin": millrace_plus.router.__file__,
                  "prefix": sys.prefix}))
"""
INSTALLED = """
import hashlib, importlib.util, json, sys
import millrace.router.execution as execution
from millrace_plus.router import forge_configuration as c
worker, probe = c.packaged_scripts()
receipt = {
    "prefix": sys.prefix, "core_origin": execution.__file__,
    "configuration_origin": c.__file__,
    "worker_sha256": hashlib.sha256(worker).hexdigest(),
    "probe_sha256": hashlib.sha256(probe).hexdigest(),
    "pinned": [c.WORKER_SHA256, c.PROBE_SHA256],
}
from millrace_plus.router.resolver import resolve_adapter
adapter = resolve_adapter("forge_runner", "millrace-forge-runner/v1")
receipt.update(
    adapter_id=adapter.adapter_id, adapter_contract=adapter.adapter_contract,
    adapter_origin=sys.modules[type(adapter).__module__].__file__,
    forge_loaded="forge" in sys.modules,
    forge_findable=importlib.util.find_spec("forge") is not None,
    scripts_imported=sorted(name for name in sys.modules
                            if name.endswith((".forge_worker", ".forge_probe"))),
)
print(json.dumps(receipt))
"""
CORE_ONLY = """
import importlib.util, json, sys
import millrace.router.control
from millrace.router.execution import EXECUTION_CONTRACT
print(json.dumps({"prefix": sys.prefix, "contract": EXECUTION_CONTRACT,
                  "core_origin": millrace.router.control.__file__,
                  "plus_findable":
                      importlib.util.find_spec("millrace_plus") is not None,
                  "forge_findable": importlib.util.find_spec("forge") is not None}))
"""


def _outside_source(path: str) -> bool:
    return not Path(path).resolve().is_relative_to(PROJECT.resolve())


def test_installed_resolver_and_assets(tmp_path):
    """Supplied final prebuilt artifacts only; unrun without a manifest."""
    manifest = supplied_artifacts()
    names = [f"millrace_plus/router/{name}" for name in FORGE_MODULES]
    with zipfile.ZipFile(manifest["plus_wheel"]["path"]) as archive:
        wheel = {name: archive.read(name) for name in names}
    pinned = pinned_constants(wheel["millrace_plus/router/forge_configuration.py"])
    worker = wheel["millrace_plus/router/forge_worker.py"]
    probe = wheel["millrace_plus/router/forge_probe.py"]
    assert hashlib.sha256(worker).hexdigest() == pinned["WORKER_SHA256"]
    assert hashlib.sha256(probe).hexdigest() == pinned["PROBE_SHA256"]
    with tarfile.open(manifest["plus_sdist"]["path"]) as archive:
        members = {
            "/".join(member.name.split("/")[1:]): member
            for member in archive.getmembers()
            if member.isfile()
        }
        for name in names:
            raw = archive.extractfile(members["src/" + name]).read()
            assert raw == wheel[name], name
        assert "docs/forge-runner-adapter.md" in members
    if manifest.get("source_assets_root"):
        source = Path(manifest["source_assets_root"])
        for name in names:
            assert (source / "src" / name).read_bytes() == wheel[name], name
    inert = child(manifest["installed_python"], INERT, tmp_path)
    assert inert["millrace_loaded"] is False and inert["forge_loaded"] is False
    assert _outside_source(inert["plus_origin"])
    receipts = {"inert": inert}
    try:
        installed = child(manifest["installed_python"], INSTALLED, tmp_path)
        receipts["installed"] = installed
    finally:
        if manifest.get("receipt_dir"):
            target = Path(manifest["receipt_dir"]) / "forge-child-origins.json"
            target.write_text(json.dumps(receipts, indent=1, sort_keys=True))
    assert (installed["adapter_id"], installed["adapter_contract"]) == (
        "forge_runner",
        "millrace-forge-runner/v1",
    )
    for key in ("core_origin", "configuration_origin", "adapter_origin"):
        assert _outside_source(installed[key]), key
        assert (
            Path(installed[key])
            .resolve()
            .is_relative_to(Path(installed["prefix"]).resolve())
        ), key
    assert (
        installed["worker_sha256"] == installed["pinned"][0] == pinned["WORKER_SHA256"]
    )
    assert installed["probe_sha256"] == installed["pinned"][1] == pinned["PROBE_SHA256"]
    assert installed["forge_loaded"] is False and installed["forge_findable"] is False
    assert installed["scripts_imported"] == []
    core = child(manifest["core_only_python"], CORE_ONLY, tmp_path)
    assert core["contract"] == "millrace-router-execution/v2"
    assert core["plus_findable"] is False and core["forge_findable"] is False
    assert _outside_source(core["core_origin"])
