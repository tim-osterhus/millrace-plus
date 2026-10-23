"""Installed candidate artifact proofs: exact local wheels, never a published runtime.

Each proof builds the current Core and Plus checkouts offline into fresh
test-owned directories, rebuilds the Core wheel from its sdist, and installs
exact local wheel files with `uv pip install --offline --no-deps --no-cache`
into separate fresh virtual environments. Installed checks run with
`PYTHONPATH` unset, `python -I` (or the installed console script), a temporary
`HOME`, and a working directory unrelated to both checkouts. Equal package-version
metadata is never treated as compatibility: offline same-version stub wheels
without (or with the wrong) execution contract must be refused.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import runpy
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest
from millrace.router import execution

PLUS_ROOT = Path(__file__).resolve().parents[2]
CORE_ROOT = Path(execution.__file__).resolve().parents[3]
VERSION = "0.23.1"
PLUS_CANDIDATE_VERSION = "0.23.1"
ROUTER_REQUIREMENT = "millrace-ai<0.24.0,>=0.23.0; extra == 'router'"
BASELINE_PLUS_WHEEL_SHA256 = (
    "5c2025e4f6d592bf4c927d659b4d729e17a62e49bc13f5b380de1b2d6bff480f"
)
BASELINE_PLUS_WHEEL = Path(os.environ.get(
    "MILLRACE_TEST_BASELINE_PLUS_WHEEL",
    str(PLUS_ROOT.parents[2] / (
        "lab/tasks/v023-canonical-cutover-plan/2026-10-21/"
        "execution/publication/plus/bootstrap-recovery/public-artifacts/"
        "millrace_plus-0.23.0-py3-none-any.whl"
    )),
))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build(source: Path, out: Path, *extra: str) -> None:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    subprocess.run(
        ["uv", "build", "--offline", "--out-dir", str(out), *extra, str(source)],
        cwd=out.parent,
        check=True,
        env=env,
        capture_output=True,
    )


def _one(folder: Path, pattern: str) -> Path:
    found = sorted(folder.glob(pattern))
    assert len(found) == 1, found
    return found[0]


def _stub_wheel(folder: Path, name: str, files: dict[str, str]) -> Path:
    """An offline same-version `millrace-ai` stand-in for a non-candidate Core."""
    folder.mkdir(parents=True)
    wheel = folder / f"millrace_ai-{VERSION}-py3-none-any.whl"
    info = f"millrace_ai-{VERSION}.dist-info"
    payload = {
        **files,
        f"{info}/METADATA": (
            f"Metadata-Version: 2.1\nName: millrace-ai\nVersion: {VERSION}\n"
            f"Summary: test-owned {name} stub\n"
        ),
        f"{info}/WHEEL": (
            "Wheel-Version: 1.0\nGenerator: plus-test\nRoot-Is-Purelib: true\n"
            "Tag: py3-none-any\n"
        ),
    }
    rows = []
    with zipfile.ZipFile(wheel, "w") as archive:
        for path, content in payload.items():
            raw = content.encode()
            archive.writestr(path, raw)
            digest = base64.urlsafe_b64encode(hashlib.sha256(raw).digest())
            rows.append(f"{path},sha256={digest.rstrip(b'=').decode()},{len(raw)}")
        archive.writestr(f"{info}/RECORD", "\n".join([*rows, f"{info}/RECORD,,"]))
    return wheel


class Env:
    """One fresh test-owned virtual environment with exact local wheels only."""

    def __init__(self, root: Path, wheels: list[Path]) -> None:
        self.root = root
        subprocess.run(
            [sys.executable, "-m", "venv", "--without-pip", str(root)],
            check=True,
            capture_output=True,
        )
        self.python = root / "bin" / "python"
        if wheels:
            self.install(*wheels)
        self.home = root.parent / f"{root.name}-home"
        self.home.mkdir()
        self.cwd = root.parent / f"{root.name}-cwd"
        self.cwd.mkdir()

    def run(self, *args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
        env = {
            "PATH": f"{self.root / 'bin'}{os.pathsep}/usr/bin{os.pathsep}/bin",
            "HOME": str(self.home),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        result = subprocess.run(
            list(args),
            cwd=self.cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if check:
            assert result.returncode == 0, (args, result.stdout, result.stderr)
        return result

    def py(self, code: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return self.run(str(self.python), "-I", "-c", code, check=check)

    def install(self, *wheels: Path, force_reinstall: bool = False) -> None:
        command = [
            "uv",
            "pip",
            "install",
            "--offline",
            "--no-deps",
            "--no-cache",
            "--python",
            str(self.python),
        ]
        if force_reinstall:
            command.append("--force-reinstall")
        command.extend(map(str, wheels))
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
        )


@pytest.fixture(scope="module")
def artifacts(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("candidate-artifacts")
    for name in ("core", "core-from-sdist", "plus", "plus-from-sdist"):
        (root / name).mkdir()
    if "MILLRACE_TEST_CORE_WHEEL" in os.environ:
        import shutil

        shutil.copy2(os.environ["MILLRACE_TEST_CORE_WHEEL"], root / "core")
        shutil.copy2(os.environ["MILLRACE_TEST_CORE_SDIST"], root / "core")
    else:
        assert (CORE_ROOT / "src/millrace/router/execution.py").is_file()
        _build(CORE_ROOT, root / "core")
    core_sdist = _one(root / "core", "*.tar.gz")
    _build(core_sdist, root / "core-from-sdist", "--wheel")
    _build(PLUS_ROOT, root / "plus")
    plus_sdist = _one(root / "plus", "*.tar.gz")
    _build(plus_sdist, root / "plus-from-sdist", "--wheel")
    return {
        "root": root,
        "core_wheel": _one(root / "core", "*.whl"),
        "core_sdist": core_sdist,
        "core_sdist_wheel": _one(root / "core-from-sdist", "*.whl"),
        "plus_wheel": _one(root / "plus", "*.whl"),
        "plus_sdist": plus_sdist,
        "plus_sdist_wheel": _one(root / "plus-from-sdist", "*.whl"),
    }


def _record(wheel: Path) -> dict[str, str]:
    with zipfile.ZipFile(wheel) as archive:
        record = next(n for n in archive.namelist() if n.endswith(".dist-info/RECORD"))
        return {
            line.split(",")[0]: line.split(",")[1]
            for line in archive.read(record).decode().splitlines()
            if line and not line.split(",")[0].endswith("RECORD")
        }


FAKE_PI = r"""#!{python}
import json, os, sys
if "--version" in sys.argv:
    print("0.87.0")
    sys.exit(0)
assert os.environ["PI_CODING_AGENT_DIR"] == {agent!r}
assert sys.argv[-1].startswith("@")
cwd = os.getcwd()
message = {{
    "role": "assistant",
    "content": [{{"type": "text",
                  "text": '{{"outcome":"READY","summary":"done","refs":[]}}'}}],
    "api": "test-api", "provider": "fixture", "model": "exact-model",
    "usage": {{"input": 1, "output": 1, "cacheRead": 0, "cacheWrite": 0,
               "totalTokens": 2, "cost": {{"input": 0, "output": 0,
               "cacheRead": 0, "cacheWrite": 0, "total": 0}}}},
    "stopReason": "stop", "timestamp": 1,
}}
events = [
    {{"type": "session", "version": 3, "id": "fake", "timestamp": "t", "cwd": cwd}},
    {{"type": "agent_start"}}, {{"type": "turn_start"}},
    {{"type": "message_start", "message": {{"role": "assistant", "content": []}}}},
    {{"type": "message_end", "message": message}},
    {{"type": "turn_end", "message": message}},
    {{"type": "agent_end", "messages": [message], "willRetry": False}},
    {{"type": "agent_settled"}},
]
os.write(1, b"".join(json.dumps(e).encode() + b"\n" for e in events))
"""

DRIVER = r"""
import json, sys, uuid
from pathlib import Path
import millrace.router.execution as execution
import millrace_plus.router.control as control
import millrace_plus.router.pi as pi
root = Path(sys.argv[1])
workflow = root / "workflow" / "workflow.json"
project, home = root / "project", root / "state"
created = control.create_instance(
    workflow, project, state_home=home, request_id=uuid.uuid4().hex,
    expected_state_version=0, host_paths={"agent": str(project / "agent")},
)
assert created["status"] == "applied", created
instance = created["instance_id"]
started = control.start_run(
    instance, state_home=home, request_id=uuid.uuid4().hex, expected_state_version=1
)
run = control.inspect_run(instance, started["run_id"], state_home=home)
print(json.dumps({
    "instance": instance, "run": started["run_id"], "status": run["status"],
    "count": run["count"], "adapter": run["invocations"][0]["launch"]["adapter_id"],
    "origin": run["invocations"][0]["origin"],
    "execution_file": execution.__file__, "pi_file": pi.__file__,
    "control_file": control.__file__,
}))
"""


def _pi_project(root: Path, python: Path) -> Path:
    workflow = root / "workflow"
    project = root / "project"
    (project / "agent").mkdir(parents=True)
    workflow.mkdir()
    fake = workflow / "fake-pi"
    fake.write_text(
        FAKE_PI.format(python=python, agent=str((project / "agent").resolve()))
    )
    fake.chmod(0o700)
    (workflow / "task.txt").write_text("Review this project.")
    (workflow / "pi.json").write_text(
        json.dumps(
            {
                "format": "millrace-pi-json-profile/v1",
                "pi_version": "0.87.0",
                "agent_dir": "agent",
                "provider": "fixture",
                "model": "exact-model",
                "thinking": "off",
            }
        )
    )
    graph = {
        "format": "millrace-router/v3",
        "limits": {"max_invocations": 3, "max_elapsed_seconds": 60},
        "bindings": {
            "native": {
                "adapter": "pi_json",
                "contract": "millrace-pi-json/v1",
                "executable": str(fake),
                "config": {"profile": "pi.json"},
            }
        },
        "control_files": ["pi.json", "task.txt"],
        "defaults": {"timeout_seconds": 20},
        "planes": {
            "main": {
                "workspace": ".",
                "start": "step",
                "nodes": {
                    "step": {
                        "invoke": {
                            "binding": "native",
                            "config": {"prompt": "task.txt"},
                        },
                        "result": "json-v1",
                        "outcomes": ["READY"],
                        "on": {"READY": {"finish": "succeeded"}},
                        "on_error": {
                            "pause": {"reason": "execution_error", "resume": "step"}
                        },
                        "on_unknown": {
                            "pause": {"reason": "unknown", "resume": "step"}
                        },
                    }
                },
            }
        },
    }
    (workflow / "workflow.json").write_text(json.dumps(graph))
    return root


def _command_project(root: Path) -> Path:
    workflow = root / "workflow"
    project = root / "project"
    workflow.mkdir(parents=True)
    project.mkdir()
    (workflow / "step.py").write_text('print(\'{"outcome":"DONE"}\')\n')
    graph = {
        "format": "millrace-router/v3",
        "limits": {"max_invocations": 3, "max_elapsed_seconds": 60},
        "bindings": {
            "python": {
                "adapter": "command",
                "contract": "millrace-command/v1",
                "executable": "python3",
                "config": {},
            }
        },
        "control_files": ["step.py"],
        "defaults": {"timeout_seconds": 20},
        "planes": {
            "main": {
                "workspace": ".",
                "start": "step",
                "nodes": {
                    "step": {
                        "invoke": {
                            "binding": "python",
                            "config": {"script": "step.py", "args": []},
                            "cwd": ".",
                            "inputs": [],
                        },
                        "result": "json-v1",
                        "outcomes": ["DONE"],
                        "on": {"DONE": {"finish": "succeeded"}},
                        "on_error": {
                            "pause": {"reason": "execution_error", "resume": "step"}
                        },
                        "on_unknown": {
                            "pause": {"reason": "unknown", "resume": "step"}
                        },
                    }
                },
            }
        },
    }
    (workflow / "workflow.json").write_text(json.dumps(graph))
    return root


def _last_json(text: str) -> dict[str, object]:
    value = json.loads(text.strip().splitlines()[-1])
    assert isinstance(value, dict)
    return value


def _tree(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): _sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_matched_candidate_wheels_and_public_controls(
    artifacts: dict[str, Path],
) -> None:
    root = artifacts["root"]
    source = root / "nested-peer-source"
    source.mkdir()
    for name in (
        "LICENSE", "README.md", "pyproject.toml", "src", "tests", "docs", ".github"
    ):
        original = PLUS_ROOT / name
        if original.is_dir():
            shutil.copytree(
                original, source / name, ignore=shutil.ignore_patterns("__pycache__")
            )
        else:
            shutil.copyfile(original, source / name)
    peer = source / ".ci-os"
    peer.mkdir()
    for name in ("LICENSE", "README.md", "pyproject.toml"):
        (peer / name).write_text(f"Unrelated peer {name}\n")
    nested_dist = root / "nested-peer-dist"
    nested_dist.mkdir()
    _build(source, nested_dist, "--sdist")
    with tarfile.open(_one(nested_dist, "*.tar.gz")) as archive:
        prefix = f"millrace_plus-{PLUS_CANDIDATE_VERSION}/"
        assert not any("/.ci-os/" in name for name in archive.getnames())
        for name in ("LICENSE", "README.md", "pyproject.toml"):
            member = archive.extractfile(prefix + name)
            assert member is not None
            assert member.read() == (PLUS_ROOT / name).read_bytes()
    plus_wheel = artifacts["plus_wheel"]
    # Generic Core payload: identical from checkout and from the rebuilt sdist.
    assert _record(artifacts["core_wheel"]) == _record(artifacts["core_sdist_wheel"])
    assert not any(
        name.startswith("millrace_plus/") or name.endswith("/pi.py")
        for name in _record(artifacts["core_wheel"])
    )
    # Plus payload: optional router modules, no Core copy, one-way extra.
    with zipfile.ZipFile(plus_wheel) as archive:
        names = set(archive.namelist())
        info = next(n for n in names if n.endswith(".dist-info/METADATA"))
        metadata = archive.read(info).decode()
        entry = archive.read(info.replace("METADATA", "entry_points.txt")).decode()
    assert not any(name.startswith("millrace/") for name in names)
    for module in (
        "__init__", "__main__", "cli", "configuration", "control", "pi",
        "codex", "codex_configuration", "claude",
        "opencode", "opencode_configuration", "resolver",
    ):
        assert f"millrace_plus/router/{module}.py" in names
    for asset in (
        "__init__.py", "paperclip_bridge.py", "paperclip_records.py",
        "paperclip_http.py",
        "paperclip/package.json", "paperclip/index.js", "paperclip/README.md",
    ):
        assert f"millrace_plus/integrations/{asset}" in names
    assert [
        line for line in metadata.splitlines() if line.startswith("Requires-Dist:")
    ] == [f"Requires-Dist: {ROUTER_REQUIREMENT}"]
    assert "Provides-Extra: router" in metadata.splitlines()
    assert entry.split() == [
        "[console_scripts]",
        "millrace-plus",
        "=",
        "millrace_plus.router.cli:cli",
    ]

    # Core-only environment: generic CLI works and Plus is absent.
    core_only = Env(root / "core-only", [artifacts["core_wheel"]])
    absent = core_only.py(
        "import importlib.util, importlib.metadata as m\n"
        "assert importlib.util.find_spec('millrace_plus') is None\n"
        "assert importlib.util.find_spec('millrace_workflow_package') is None\n"
        "try:\n m.distribution('millrace-plus')\nexcept m.PackageNotFoundError:\n"
        " print('absent')\n"
    )
    assert absent.stdout.strip() == "absent"
    generic = _command_project(core_only.cwd / "generic")
    home = generic / "state"
    created = core_only.run(
        str(core_only.root / "bin" / "millrace"),
        "router",
        "instance",
        "create",
        "--state-home",
        str(home),
        "--workflow",
        str(generic / "workflow" / "workflow.json"),
        "--project",
        str(generic / "project"),
        check=True,
    )
    instance = str(_last_json(created.stdout)["instance_id"])
    started = core_only.run(
        str(core_only.python),
        "-I",
        "-m",
        "millrace.router.cli",
        "start",
        "--state-home",
        str(home),
        "--instance",
        instance,
        check=True,
    )
    assert _last_json(started.stdout)["status"] == "applied"

    # Matched environment: sdist-rebuilt Core wheel + Plus wheel.
    matched = Env(root / "matched", [artifacts["core_sdist_wheel"], plus_wheel])
    provenance = _last_json(
        matched.py(
            "import json, importlib.metadata as m\n"
            "import millrace.router.execution as e, millrace_plus.router.pi as p\n"
            "print(json.dumps({'core': e.__file__, 'plus': p.__file__,\n"
            " 'contract': e.EXECUTION_CONTRACT,\n"
            " 'core_url': json.loads(m.distribution('millrace-ai')"
            ".read_text('direct_url.json'))['url'],\n"
            " 'plus_url': json.loads(m.distribution('millrace-plus')"
            ".read_text('direct_url.json'))['url'],\n"
            " 'versions': [m.version('millrace-ai'), m.version('millrace-plus')]}))\n"
        ).stdout
    )
    site = str(matched.root)
    assert str(provenance["core"]).startswith(site)
    assert str(provenance["plus"]).startswith(site)
    for checkout in (CORE_ROOT, PLUS_ROOT):
        assert not str(provenance["core"]).startswith(str(checkout))
        assert not str(provenance["plus"]).startswith(str(checkout))
    assert provenance["contract"] == "millrace-router-execution/v2"
    assert provenance["core_url"] == artifacts["core_sdist_wheel"].as_uri()
    assert provenance["plus_url"] == plus_wheel.as_uri()
    assert provenance["versions"] == [VERSION, PLUS_CANDIDATE_VERSION]

    project = _pi_project(matched.cwd / "pi", matched.python)
    driver = matched.cwd / "driver.py"
    driver.write_text(DRIVER)
    result = _last_json(
        matched.run(
            str(matched.python), "-I", str(driver), str(project), check=True
        ).stdout
    )
    assert (result["status"], result["count"]) == ("succeeded", 1)
    assert (result["adapter"], result["origin"]) == ("pi_json", "adapter")
    for key in ("execution_file", "pi_file", "control_file"):
        assert str(result[key]).startswith(site)

    home = project / "state"
    common = ["--state-home", str(home), "--instance", str(result["instance"])]
    status = matched.run(
        str(matched.python),
        "-I",
        "-m",
        "millrace_plus.router",
        "status",
        *common,
        "--run",
        str(result["run"]),
        check=True,
    )
    assert _last_json(status.stdout)["status"] == "succeeded"
    console = matched.run(
        str(matched.root / "bin" / "millrace-plus"),
        "router",
        "validate",
        *common,
        check=True,
    )
    assert _last_json(console.stdout)["valid"] is True
    usage = matched.run(str(matched.root / "bin" / "millrace-plus"), "status")
    assert usage.returncode == 2 and "millrace-plus router" in usage.stderr

    # The generic Core console in the same environment carries no Plus resolver:
    # it refuses the Plus-backed instance before any durable write.
    before = _tree(home)
    for command in (
        ["status", *common, "--run", str(result["run"])],
        ["cancel", *common, "--run", str(result["run"])],
    ):
        refused = matched.run(
            str(matched.root / "bin" / "millrace"), "router", *command
        )
        assert refused.returncode == 2, refused.stdout
        assert _last_json(refused.stderr)["code"] == "unsupported_adapter_contract"
    assert _tree(home) == before

    # Reuse the matched installed environment for the new adapter. The peer's
    # interpreter and all runtime imports come from that environment.
    from opencode_support import PROFILE, events, wire

    opencode_project = _pi_project(matched.cwd / "opencode", matched.python)
    workflow = opencode_project / "workflow"
    prompt = b"\xef\xbb\xbfReview this project.\r\nKeep exact UTF-8: \xce\xbb"
    (workflow / "task.txt").write_bytes(prompt)
    (workflow / "pi.json").write_bytes(PROFILE)
    fake = workflow / "fake-opencode"
    fake.write_text(
        f"#!{matched.python}\n"
        + (Path(__file__).parent / "fixtures/opencode_peer.py").read_text()
    )
    fake.chmod(0o700)
    Path(str(fake) + ".json").write_text(json.dumps({
        "wire": base64.b64encode(wire(events())).decode(),
        "version": base64.b64encode(b"1.19.3\n").decode(),
        "stderr": "",
        "expected_prompt": base64.b64encode(prompt).decode(),
        "expected_argv": ["run", "--format", "json"],
    }))
    graph_path = workflow / "workflow.json"
    graph = json.loads(graph_path.read_text())
    graph["bindings"]["native"].update({
        "adapter": "opencode_run", "contract": "millrace-opencode-run/v1",
        "executable": str(fake),
    })
    graph_path.write_text(json.dumps(graph))
    driver.write_text(DRIVER.replace(
        "import millrace_plus.router.pi as pi",
        "import millrace_plus.router.opencode as pi",
    ))
    native = _last_json(matched.run(
        str(matched.python), "-I", str(driver), str(opencode_project), check=True
    ).stdout)
    assert (native["status"], native["count"], native["adapter"]) == (
        "succeeded", 1, "opencode_run",
    )
    reloaded = matched.py(
        "from pathlib import Path\n"
        "from millrace_plus.router import control, opencode_configuration\n"
        "from millrace_plus.router.opencode import OpenCodeRunAdapter\n"
        "from millrace_plus.router.resolver import resolve_adapter\n"
        "assert isinstance(resolve_adapter('opencode_run', "
        "'millrace-opencode-run/v1'), OpenCodeRunAdapter)\n"
        "assert Path(opencode_configuration.__file__).is_relative_to(Path(__import__('sys').prefix))\n"  # noqa: E501 - isolated installed proof
        f"run = control.inspect_run({native['instance']!r}, {native['run']!r}, "
        f"state_home=Path({str(opencode_project / 'state')!r}))\n"
        "assert run['status'] == 'succeeded' and run['quiescent']\n"
        "assert run['completions'][0]['outcome'] == 'READY'\n"
        "assert run['completions'][0]['effective_writer'] == 'stopped'\n"
        "assert run['invocations'][0]['launch']['adapter_id'] == 'opencode_run'\n"
    )
    assert reloaded.returncode == 0
    help_result = matched.run(
        str(matched.root / "bin/millrace-plus"), "paperclip", "--help", check=True
    )
    assert "run" in help_result.stdout and "inspect" in help_result.stdout
    matched.py(
        "from pathlib import Path; import sys; "
        "from millrace_plus.integrations import paperclip_bridge; "
        "assert Path(paperclip_bridge.__file__).is_relative_to(Path(sys.prefix))"
    )


@pytest.mark.parametrize(
    "case,code",
    [
        ("absent", "core_absent"),
        ("older", "core_contract_missing"),
        ("wrong", "core_contract_mismatch"),
    ],
)
def test_plus_refuses_core_without_execution_contract(
    artifacts: dict[str, Path], case: str, code: str
) -> None:
    root = artifacts["root"] / f"handshake-{case}"
    stubs = {
        "absent": None,
        "older": {"millrace/__init__.py": '"""Older same-version Core."""\n'},
        "wrong": {
            "millrace/__init__.py": "",
            "millrace/router/__init__.py": "",
            "millrace/router/execution.py": (
                'EXECUTION_CONTRACT = "millrace-router-execution/v0"\n'
            ),
        },
    }[case]
    wheels = [artifacts["plus_wheel"]]
    if stubs is not None:
        wheels.append(_stub_wheel(root / "stub", case, stubs))
    env = Env(root / "env", wheels)
    inert = env.py(
        "import millrace_plus, millrace_plus.router, sys; "
        "from millrace_plus.integrations import paperclip_bridge; "
        "print('millrace' in sys.modules)"
    )
    assert inert.stdout.strip() == "False"
    for module in (
        "control",
        "pi",
        "configuration",
        "codex",
        "codex_configuration",
        "claude",
        "opencode",
        "opencode_configuration",
        "resolver",
    ):
        refused = env.py(
            "import importlib\n"
            "from millrace_plus.router import CoreContractError\n"
            "try:\n"
            f" importlib.import_module('millrace_plus.router.{module}')\n"
            "except CoreContractError as exc:\n"
            " print(exc.code)\n"
        )
        assert refused.stdout.strip() == code, refused.stderr
    for command in (
        [str(env.python), "-I", "-m", "millrace_plus.router", "instance", "list"],
        [str(env.root / "bin" / "millrace-plus"), "router", "instance", "list"],
    ):
        result = env.run(*command)
        assert result.returncode == 2
        assert result.stdout == ""
        assert _last_json(result.stderr)["code"] == code
        assert "Traceback" not in result.stderr


def test_codex_matched_wheels_and_sdist_public_controls(
    artifacts: dict[str, Path],
) -> None:
    """Both sdist payloads match; installed Codex uses the same public host/guard."""
    from codex_support import fake

    assert _record(artifacts["plus_wheel"]) == _record(artifacts["plus_sdist_wheel"])
    assert _record(artifacts["core_wheel"]) == _record(artifacts["core_sdist_wheel"])
    env = Env(
        artifacts["root"] / "matched-codex",
        [artifacts["core_sdist_wheel"], artifacts["plus_sdist_wheel"]],
    )
    project = _pi_project(env.cwd / "codex", env.python)
    workflow = project / "workflow"
    native = project / "project" / "codex-home"
    native.mkdir()
    (native / "auth.json").write_text("{}")
    program = fake(workflow)
    program.write_text(f"#!{env.python}\n" + program.read_text().split("\n", 1)[1])
    (workflow / "codex.json").write_text(
        json.dumps(
            {
                "format": "millrace-codex-exec-profile/v1",
                "codex_version": "0.163.0",
                "codex_home": "codex_home",
                "auth_mode": "chatgpt",
                "sandbox": "workspace-write",
                "model": "exact-model",
                "reasoning_effort": "low",
            }
        )
    )
    graph = json.loads((workflow / "workflow.json").read_text())
    graph["bindings"]["native"] = {
        "adapter": "codex_exec",
        "contract": "millrace-codex-exec/v1",
        "executable": str(program),
        "config": {"profile": "codex.json"},
    }
    graph["control_files"] = ["codex.json", "task.txt"]
    (workflow / "workflow.json").write_text(json.dumps(graph))
    driver = env.cwd / "driver.py"
    driver.write_text(
        DRIVER.replace(
            "import millrace_plus.router.pi as pi",
            "import millrace_plus.router.codex as pi",
        ).replace(
            'host_paths={"agent": str(project / "agent")}',
            'host_paths={"codex_home": str(project / "codex-home")}',
        )
    )
    result = _last_json(
        env.run(str(env.python), "-I", str(driver), str(project), check=True).stdout
    )
    assert (result["status"], result["count"], result["adapter"], result["origin"]) == (
        "succeeded",
        1,
        "codex_exec",
        "adapter",
    )
    for key in ("execution_file", "pi_file", "control_file"):
        assert str(result[key]).startswith(str(env.root))
        assert not str(result[key]).startswith(str(PLUS_ROOT))
        assert not str(result[key]).startswith(str(CORE_ROOT))
    common = [
        "--state-home",
        str(project / "state"),
        "--instance",
        str(result["instance"]),
    ]
    status = env.run(
        str(env.root / "bin/millrace-plus"),
        "router",
        "status",
        *common,
        "--run",
        str(result["run"]),
        check=True,
    )
    assert _last_json(status.stdout)["status"] == "succeeded"
    before = _tree(project / "state")
    refused = env.run(
        str(env.root / "bin/millrace"),
        "router",
        "cancel",
        *common,
        "--run",
        str(result["run"]),
    )
    assert refused.returncode == 2
    assert _last_json(refused.stderr)["code"] == "unsupported_adapter_contract"
    assert _tree(project / "state") == before
    provenance = _last_json(
        env.py(
            "import json,importlib.metadata as m\n"
            "print(json.dumps({k:json.loads(m.distribution(k)"
            ".read_text('direct_url.json'))['url'] "
            "for k in ['millrace-ai','millrace-plus']}))"
        ).stdout
    )
    assert provenance == {
        "millrace-ai": artifacts["core_sdist_wheel"].as_uri(),
        "millrace-plus": artifacts["plus_sdist_wheel"].as_uri(),
    }


def test_claude_matched_artifacts_and_public_controls(
    artifacts: dict[str, Path],
) -> None:
    """Exact accepted Core wheel plus rebuilt Plus exercises the third resolver pair."""
    from claude_support import fake

    frozen = Path(os.environ.get("MILLRACE_TEST_CORE_WHEEL", artifacts["core_wheel"]))
    assert _sha256(frozen) == _sha256(artifacts["core_wheel"])
    assert _record(artifacts["plus_wheel"]) == _record(artifacts["plus_sdist_wheel"])
    env = Env(
        artifacts["root"] / "matched-claude", [frozen, artifacts["plus_sdist_wheel"]]
    )
    project = _pi_project(env.cwd / "claude", env.python)
    workflow = project / "workflow"
    program = fake(workflow)
    program.write_text(f"#!{env.python}\n" + program.read_text().split("\n", 1)[1])
    (workflow / "claude.json").write_text(
        json.dumps(
            {"format": "millrace-claude-print-profile/v1", "claude_version": "2.1.296"}
        )
    )
    graph = json.loads((workflow / "workflow.json").read_text())
    graph["bindings"]["native"] = {
        "adapter": "claude_print",
        "contract": "millrace-claude-print/v1",
        "executable": str(program),
        "config": {"profile": "claude.json"},
    }
    graph["control_files"] = ["claude.json", "task.txt"]
    (workflow / "workflow.json").write_text(json.dumps(graph))
    driver = env.cwd / "driver.py"
    driver.write_text(
        DRIVER.replace(
            "import millrace_plus.router.pi as pi",
            "import millrace_plus.router.claude as pi",
        ).replace('host_paths={"agent": str(project / "agent")}', "host_paths={}")
    )
    result = _last_json(
        env.run(str(env.python), "-I", str(driver), str(project), check=True).stdout
    )
    assert (result["status"], result["adapter"]) == ("succeeded", "claude_print")
    for key in ("execution_file", "pi_file", "control_file"):
        assert str(result[key]).startswith(str(env.root))
    observed = env.py(
        "import json,importlib.metadata as m\n"
        "print(json.dumps({k:json.loads(m.distribution(k).read_text('direct_url.json'))['url'] "  # noqa: E501 - embedded isolated proof
        "for k in ['millrace-ai','millrace-plus']}))"
    )
    assert _last_json(observed.stdout) == {
        "millrace-ai": frozen.as_uri(),
        "millrace-plus": artifacts["plus_sdist_wheel"].as_uri(),
    }
    core = Env(artifacts["root"] / "claude-core-only", [frozen])
    proof = core.py(
        "import importlib.util,json,sys,uuid\n"
        "from pathlib import Path\n"
        "from millrace.router import control\n"
        "assert importlib.util.find_spec('millrace_plus') is None\n"
        "root=Path.cwd();(root/'project').mkdir();(root/'workflow').mkdir()\n"
        "g=json.loads(" + repr(json.dumps(graph)) + ")\n"
        "g['control_files']=[]\n"
        "g['bindings']['native']={'adapter':'command','contract':'millrace-command/v1','executable':sys.executable,'config':{}}\n"
        "g['planes']['main']['nodes']['step']['invoke']['config']={'args':['-c','import json;print(json.dumps({\"outcome\":\"READY\"}))']}\n"  # noqa: E501 - embedded isolated proof
        "(root/'workflow/workflow.json').write_text(json.dumps(g))\n"
        "made=control.create_instance(root/'workflow/workflow.json',root/'project',state_home=root/'state',request_id=uuid.uuid4().hex,expected_state_version=0)\n"
        "assert made['status']=='applied',made\n"
        "run=control.start_run(made['instance_id'],state_home=root/'state',request_id=uuid.uuid4().hex,expected_state_version=1)\n"
        "assert control.inspect_run(made['instance_id'],run['run_id'],state_home=root/'state')['status']=='succeeded'\n"  # noqa: E501 - embedded isolated proof
        "print(json.dumps({'core_only_command':'passed'}))"
    )
    assert _last_json(proof.stdout)["core_only_command"] == "passed"


def test_advisory_commands_and_json_result_use_current_public_controls(
    tmp_path: Path,
) -> None:
    """The packaged prose is executable against current parsers and controls."""
    import re
    import uuid

    from millrace.router import control

    from millrace_plus.router import cli

    skills = PLUS_ROOT / "src/millrace_plus/skills"
    for skill in skills.glob("*/SKILL.md"):
        text = skill.read_text()
        for line in text.splitlines():
            if line.startswith("millrace-plus router "):
                with pytest.raises(SystemExit) as raised:
                    cli.main(line.split()[2:])
                assert raised.value.code == 0
        for index, block in enumerate(re.findall(r"```json\n(.*?)\n```", text, re.S)):
            root = _command_project(tmp_path / skill.parent.name / str(index))
            graph_path = root / "workflow/workflow.json"
            graph = json.loads(graph_path.read_text())
            config = graph["planes"]["main"]["nodes"]["step"]["invoke"]["config"]
            config.clear()
            config["args"] = ["-c", f"print({block!r})"]
            graph_path.write_text(json.dumps(graph))
            home = root / "state"
            made = control.create_instance(
                graph_path,
                root / "project",
                state_home=home,
                request_id=uuid.uuid4().hex,
                expected_state_version=0,
            )
            assert made["status"] == "applied", made
            started = control.start_run(
                made["instance_id"],
                state_home=home,
                request_id=uuid.uuid4().hex,
                expected_state_version=1,
            )
            result = control.inspect_run(
                made["instance_id"], started["run_id"], state_home=home
            )
            assert result["status"] == "succeeded", result


def test_package_update_keeps_copies_independent(artifacts: dict[str, Path]) -> None:
    assert BASELINE_PLUS_WHEEL.is_file()
    assert _sha256(BASELINE_PLUS_WHEEL) == BASELINE_PLUS_WHEEL_SHA256
    root = artifacts["root"]
    env = Env(root / "catalog-update", [BASELINE_PLUS_WHEEL])
    console = str(env.root / "bin/millrace-plus")
    assert env.py(
        "import importlib.metadata as m; "
        "assert m.version('millrace-plus') == '0.23.0'; "
        "import importlib.util; "
        "assert importlib.util.find_spec('millrace') is None"
    ).returncode == 0

    catalog_home = env.cwd / "catalog"
    catalog_home.mkdir()
    env.run(console, "catalog", "home", "set", str(catalog_home), check=True)
    copy_a = env.cwd / "copy-a"
    initialized = _last_json(
        env.run(
            console,
            "catalog",
            "init",
            "bundled:software-change",
            str(copy_a),
            check=True,
        ).stdout
    )
    assert initialized["source"].endswith("/millrace_plus/workflows/software-change")
    graph = json.loads((copy_a / "workflow.json").read_text())
    prompt = graph["planes"]["main"]["nodes"]["work"]["invoke"]["config"][
        "prompt"
    ]
    (copy_a / prompt).write_bytes((copy_a / prompt).read_bytes() + b"\nlocal edit\n")
    copy_a_before = _tree(copy_a)

    copy_sibling = env.cwd / "copy-sibling"
    env.run(
        console,
        "catalog",
        "init",
        "bundled:software-change",
        str(copy_sibling),
        check=True,
    )
    copy_sibling_before = _tree(copy_sibling)

    local_variant = catalog_home / "local-variant"
    bundled_source = env.py(
        "from millrace_plus import catalog; "
        "print(catalog.bundled_root() / 'software-change')"
    ).stdout.strip()
    env.run(
        console,
        "catalog",
        "copy",
        bundled_source,
        str(local_variant),
        check=True,
    )
    (local_variant / prompt).write_bytes(
        (local_variant / prompt).read_bytes() + b"\nlocal catalog edit\n"
    )
    local_before = _tree(local_variant)
    settings = env.home / ".config/millrace-plus/catalog.json"
    settings_before = settings.read_bytes()

    env.install(artifacts["plus_wheel"], force_reinstall=True)
    assert env.py(
        "import importlib.metadata as m; "
        "assert m.version('millrace-plus') == '0.23.1'; "
        "import importlib.util; "
        "assert importlib.util.find_spec('millrace') is None"
    ).returncode == 0
    assert _tree(copy_a) == copy_a_before
    assert _tree(copy_sibling) == copy_sibling_before
    assert _tree(local_variant) == local_before
    assert settings.read_bytes() == settings_before
    selectors = [
        item["selector"]
        for item in _last_json(env.run(console, "catalog", "list", check=True).stdout)[
            "templates"
        ]
    ]
    assert "local:local-variant" in selectors

    copy_b = env.cwd / "copy-b"
    initialized_b = _last_json(
        env.run(
            console,
            "catalog",
            "init",
            "bundled:software-change",
            str(copy_b),
            check=True,
        ).stdout
    )
    candidate_source = env.py(
        "from millrace_plus import catalog; "
        "print(catalog.bundled_root() / 'software-change')"
    ).stdout.strip()
    candidate_digest = env.py(
        "from millrace_plus import catalog; "
        "print(catalog.workflow_fingerprint("
        "catalog.bundled_root() / 'software-change'))"
    ).stdout.strip()
    copy_digest = env.py(
        "from pathlib import Path; from millrace_plus import catalog; "
        f"print(catalog.workflow_fingerprint(Path({str(copy_b)!r})))"
    ).stdout.strip()
    assert initialized_b["provenance"] == {
        "template_id": "bundled:software-change",
        "plus_version": "0.23.1",
        "sha256": candidate_digest,
    }
    assert copy_digest == candidate_digest
    assert _tree(copy_b) == _tree(Path(candidate_source))
    assert _tree(copy_sibling) == copy_sibling_before

    # The post-upgrade copy can enter the runtime through its command-only first
    # node. Use Plus's public control facade so the stock Claude binding is
    # resolved, then verify the run pauses before reaching that native stage.
    env.install(artifacts["core_wheel"])
    # Core resolves every declared binding at create time, including the
    # unreachable Claude stage. Bind the stock entries to this environment's
    # Python and a test-owned inert Claude executable through public host_paths;
    # only the command binding should run.
    inert_claude = env.cwd / "claude-not-launched"
    inert_claude.write_text("#!/bin/sh\nexit 97\n", encoding="ascii")
    inert_claude.chmod(0o700)
    project_b = env.cwd / "copy-b-project"
    project_b.mkdir()
    usable = env.py(
        "import json, sys, uuid\n"
        "from pathlib import Path\n"
        "from millrace_plus.router import control\n"
        f"workflow = Path({str(copy_b)!r})\n"
        f"project = Path({str(project_b)!r})\n"
        f"state = Path({str(env.cwd / 'copy-b-state')!r})\n"
        f"inert_claude = Path({str(inert_claude)!r})\n"
        "created = control.create_instance(\n"
        "    workflow / 'workflow.json', project, state_home=state,\n"
        "    request_id=uuid.uuid4().hex, expected_state_version=0,\n"
        "    host_paths={'claude': str(inert_claude), 'python3': sys.executable})\n"
        "assert created['status'] == 'applied', created\n"
        "started = control.start_run(\n"
        "    created['instance_id'], state_home=state,\n"
        "    request_id=uuid.uuid4().hex, expected_state_version=1)\n"
        "run = control.inspect_run(\n"
        "    created['instance_id'], started['run_id'], state_home=state)\n"
        "print(json.dumps({\n"
        "    'status': run['status'],\n"
        "    'pause_reason': run['pause_reason'],\n"
        "    'count': run['count'],\n"
        "    'plane_status': run['planes']['main']['status'],\n"
        "    'plane_pause_reason': run['planes']['main']['pause_reason'],\n"
        "    'outcome': run['completions'][0]['outcome'],\n"
        "    'quiescent': run['quiescent'],\n"
        "    'inflight': run['inflight_invocations'],\n"
        "    'writers': [item['writer_state'] for item in run['invocations']],\n"
        "    'effective_writers': [\n"
        "        item['effective_writer'] for item in run['completions']],\n"
        "}))\n"
    )
    assert _last_json(usable.stdout) == {
        "status": "paused",
        "pause_reason": "no_runnable_planes",
        "count": 1,
        "plane_status": "paused",
        "plane_pause_reason": "request_missing_or_empty",
        "outcome": "NEEDS_INPUT",
        "quiescent": True,
        "inflight": [],
        "writers": ["stopped"],
        "effective_writers": ["stopped"],
    }
    assert not (project_b / "request.md").exists()
    assert not (copy_b / "request.md").exists()
    assert _tree(copy_b) == _tree(Path(candidate_source))

    sibling_validation = env.cwd / "copy-sibling-validation"
    env.run(
        console,
        "catalog",
        "copy",
        str(copy_sibling),
        str(sibling_validation),
        check=True,
    )
    assert _tree(sibling_validation) == copy_sibling_before


@pytest.mark.parametrize("workflow", ["document-review", "software-change"])
def test_stock_inputs_map_exact_request_bytes_and_refuse_conflicts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    workflow: str,
) -> None:
    project = tmp_path / workflow
    project.mkdir()
    monkeypatch.chdir(project)
    script = (
        PLUS_ROOT
        / "src/millrace_plus/workflows"
        / workflow
        / "scripts/check_inputs.py"
    )
    request_markdown = "Goal café\nAcceptance criteria with trailing spaces.  \n"
    manifest = {
        "format": "millrace-plus-subworkflow-inputs/v1",
        "identity": {"call_key": "review-1"},
        "inputs": {"request_markdown": request_markdown},
    }
    (project / "subworkflow-inputs.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )

    runpy.run_path(str(script))
    first = _last_json(capsys.readouterr().out)
    expected = request_markdown.encode("utf-8")
    assert first["outcome"] == "READY"
    assert (project / "request.md").read_bytes() == expected

    runpy.run_path(str(script))
    assert _last_json(capsys.readouterr().out)["outcome"] == "READY"
    assert (project / "request.md").read_bytes() == expected

    (project / "request.md").write_bytes(b"operator-owned request\n")
    runpy.run_path(str(script))
    conflict = _last_json(capsys.readouterr().out)
    assert conflict["outcome"] == "NEEDS_INPUT"
    assert (project / "request.md").read_bytes() == b"operator-owned request\n"

    (project / "request.md").unlink()
    outside = tmp_path / "outside.md"
    outside.write_bytes(expected)
    (project / "request.md").symlink_to(outside)
    runpy.run_path(str(script))
    linked = _last_json(capsys.readouterr().out)
    assert linked["outcome"] == "NEEDS_INPUT"
    assert outside.read_bytes() == expected


@pytest.mark.parametrize("workflow", ["document-review", "software-change"])
def test_stock_inputs_reject_blank_manifest_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    workflow: str,
) -> None:
    project = tmp_path / workflow
    project.mkdir()
    monkeypatch.chdir(project)
    script = (
        PLUS_ROOT
        / "src/millrace_plus/workflows"
        / workflow
        / "scripts/check_inputs.py"
    )
    (project / "subworkflow-inputs.json").write_text(
        json.dumps(
            {
                "format": "millrace-plus-subworkflow-inputs/v1",
                "identity": {"call_key": "review-1"},
                "inputs": {"request_markdown": " \n\t "},
            }
        ),
        encoding="utf-8",
    )

    runpy.run_path(str(script))

    assert _last_json(capsys.readouterr().out)["outcome"] == "NEEDS_INPUT"
    assert not (project / "request.md").exists()


@pytest.mark.parametrize("workflow", ["document-review", "software-change"])
@pytest.mark.parametrize(
    "manifest_bytes",
    [
        pytest.param(
            b'{"format":"millrace-plus-subworkflow-inputs/v1",'
            b'"identity":{"call_key":"first"},'
            b'"identity":{"call_key":"second"},'
            b'"inputs":{"request_markdown":"selected request"}}',
            id="duplicate-top-level-key",
        ),
        pytest.param(
            b'{"format":"millrace-plus-subworkflow-inputs/v1",'
            b'"identity":{"call_key":"review-1"},'
            b'"inputs":{"request_markdown":"other",'
            b'"request_markdown":"selected request"}}',
            id="duplicate-nested-input-key",
        ),
        pytest.param(
            b'{"format":"millrace-plus-subworkflow-inputs/v1",'
            b'"identity":{"call_key":"review-1"},'
            b'"inputs":{"request_markdown":"selected request",'
            b'"extra":NaN}}',
            id="nan",
        ),
        pytest.param(
            b'{"format":"millrace-plus-subworkflow-inputs/v1",'
            b'"identity":{"call_key":"review-1"},'
            b'"inputs":{"request_markdown":"selected request",'
            b'"extra":Infinity}}',
            id="infinity",
        ),
        pytest.param(
            b'{"format":"millrace-plus-subworkflow-inputs/v1",'
            b'"identity":{"call_key":"review-1"},'
            b'"inputs":{"request_markdown":"selected request",'
            b'"extra":"invalid-utf8-\xff"}}',
            id="malformed-utf8",
        ),
    ],
)
@pytest.mark.parametrize(
    "existing_request",
    [
        pytest.param(False, id="no-request"),
        pytest.param(True, id="existing-request"),
    ],
)
def test_stock_inputs_reject_ambiguous_manifest_json_without_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    workflow: str,
    manifest_bytes: bytes,
    existing_request: bool,
) -> None:
    project = tmp_path / workflow
    project.mkdir()
    monkeypatch.chdir(project)
    script = (
        PLUS_ROOT
        / "src/millrace_plus/workflows"
        / workflow
        / "scripts/check_inputs.py"
    )
    request_path = project / "request.md"
    original = b"selected request"
    if existing_request:
        request_path.write_bytes(original)
    (project / "subworkflow-inputs.json").write_bytes(manifest_bytes)

    runpy.run_path(str(script))

    assert _last_json(capsys.readouterr().out)["outcome"] == "NEEDS_INPUT"
    if existing_request:
        assert request_path.read_bytes() == original
    else:
        assert not request_path.exists()
