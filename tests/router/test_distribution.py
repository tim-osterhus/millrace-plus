"""Installed candidate artifact proofs: exact local wheels, never a published runtime.

Each proof builds the current Core and Plus checkouts offline into fresh
test-owned directories, rebuilds the Core wheel from its sdist, and installs
exact local wheel files with `uv pip install --offline --no-deps --no-cache`
into separate fresh virtual environments. Installed checks run with
`PYTHONPATH` unset, `python -I` (or the installed console script), a temporary
`HOME`, and a working directory unrelated to both checkouts. Equal `0.23.0`
metadata is never treated as compatibility: offline same-version stub wheels
without (or with the wrong) execution contract must be refused.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from millrace.router import execution

PLUS_ROOT = Path(__file__).resolve().parents[2]
CORE_ROOT = Path(execution.__file__).resolve().parents[3]
VERSION = "0.23.0"
ROUTER_REQUIREMENT = "millrace-ai<0.24.0,>=0.23.0; extra == 'router'"


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
            subprocess.run(
                [
                    "uv",
                    "pip",
                    "install",
                    "--offline",
                    "--no-deps",
                    "--no-cache",
                    "--python",
                    str(self.python),
                    *map(str, wheels),
                ],
                check=True,
                capture_output=True,
                env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
            )
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
    for module in ("__init__", "__main__", "cli", "configuration", "control", "pi"):
        assert f"millrace_plus/router/{module}.py" in names
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
    assert provenance["versions"] == [VERSION, VERSION]

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
    inert = env.py("import millrace_plus, sys; print('millrace' in sys.modules)")
    assert inert.stdout.strip() == "False"
    for module in (
        "control",
        "pi",
        "configuration",
        "codex",
        "codex_configuration",
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
