"""Owned CI/CD fixtures: a real Git target with a real defect and a real check.

Every helper writes only below the caller's temporary directory. Nothing here
contacts a remote, configures global Git or edits Core stores directly.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
GIT_ENV = {
    "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
}
DEFECT = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"
CHECK = (
    "from pathlib import Path\n"
    "import sys\n"
    "source = Path('src/calc.py')\n"
    "namespace = {}\n"
    "exec(compile(source.read_bytes(), str(source), 'exec'), namespace)\n"
    "add = namespace['add']\n"
    "if add(2, 3) != 5:\n"
    "    print(f'add(2, 3) returned {add(2, 3)}, expected 5')\n"
    "    sys.exit(1)\n"
    "print('add(2, 3) == 5')\n"
)


def git(target: Path, *args: str) -> str:
    child = subprocess.run(
        ["git", "-C", str(target), *args],
        env=GIT_ENV,
        capture_output=True,
        timeout=30,
        text=True,
    )
    assert child.returncode == 0, child.stderr
    return child.stdout


def make_target(root: Path) -> tuple[Path, str]:
    """Commit a small project whose configured check fails before repair."""
    target = root / "target"
    (target / "src").mkdir(parents=True)
    (target / "checks").mkdir()
    (target / "src" / "calc.py").write_text(DEFECT)
    (target / "src" / "notes.txt").write_text("calculator notes\n")
    (target / "checks" / "check_calc.py").write_text(CHECK)
    (target / "pyproject.toml").write_text("[project]\nname = 'fixture'\n")
    git(target, "init", "--quiet")
    git(target, "add", "--", ".")
    git(
        target,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "fixture baseline",
    )
    return target, git(target, "rev-parse", "HEAD").strip()


def run_check(target: Path) -> subprocess.CompletedProcess[str]:
    """The exact configured check argv, run by the test for RED/GREEN proof."""
    return subprocess.run(
        [sys.executable, "checks/check_calc.py"],
        cwd=target,
        capture_output=True,
        text=True,
        timeout=30,
    )


def peer_executable(root: Path) -> Path:
    """Executable wrapper for the deterministic command peer (role adapter)."""
    path = root / "bin" / "cicd-peer"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"#!{sys.executable}\n"
        "import runpy, sys\n"
        f"sys.argv[0] = {str(FIXTURES / 'cicd_peer.py')!r}\n"
        f"runpy.run_path({str(FIXTURES / 'cicd_peer.py')!r}, run_name='__main__')\n"
    )
    path.chmod(0o700)
    return path


def config(target: Path, executable: Path, **changes: object) -> dict[str, object]:
    data: dict[str, object] = {
        "format": "millrace-plus-cicd-config/v1",
        "target_root": str(target),
        "repository": "example/calc",
        "repair_paths": ["src"],
        "protected_paths": [],
        "check_commands": [
            {
                "id": "calc",
                "argv": [sys.executable, "checks/check_calc.py"],
                "cwd": ".",
                "timeout_seconds": 60,
                "expected_exit_code": 0,
            }
        ],
        "repair_binding": {"adapter": "command", "executable": str(executable)},
        "external_policy": {"mode": "hold", "effects": []},
    }
    data.update(changes)
    return data


def write_json(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")
    return path


def event(
    root: Path,
    head: str,
    *,
    name: str = "event",
    conclusion: str = "failure",
    summary: str = "SCENARIO:small check calc failed",
    attempt: int = 1,
    run_id: str = "1001",
    log: bytes | None = b"add(2, 3) returned -1, expected 5\n",
    occurred_at: str = "2026-10-30T00:00:00Z",
) -> Path:
    """Write one event file and its referenced log in a fresh directory."""
    directory = root / "events" / name
    directory.mkdir(parents=True)
    log_ref = None
    if log is not None:
        (directory / "ci.log").write_bytes(log)
        log_ref = {"path": "ci.log", "sha256": hashlib.sha256(log).hexdigest()}
    return write_json(
        directory / "event.json",
        {
            "format": "millrace-plus-cicd-event/v1",
            "source": "github-actions",
            "repository": "example/calc",
            "run_id": run_id,
            "attempt": attempt,
            "job_id": "check",
            "head_sha": head,
            "conclusion": conclusion,
            "summary": summary,
            "log": log_ref,
            "occurred_at": occurred_at,
        },
    )


def evidence(root: Path, text: str = "Operator reviewed the held report.\n") -> Path:
    path = root / "decisions" / (uuid.uuid4().hex + ".txt")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def new_id() -> str:
    return uuid.uuid4().hex


def idle_read_barrier(
    monkeypatch: pytest.MonkeyPatch, root: Path
) -> tuple[Path, Path]:
    """Gate the real idle loop's first public read in an owned workflow copy.

    Core remains unmodified. The test can prove an applied pause while the
    actual wait invocation is in flight, then release an already-applied view.
    """
    from millrace_plus import catalog

    ready, release = root / "idle-ready.json", root / "idle-release"
    original_init = catalog.init_template
    wrapper = f'''
# Test-owned readiness barrier, added before Core captures this workflow copy.
import time as _barrier_time
from millrace_plus.cicd_runtime import Runtime as _Runtime
_original_wait = _Runtime._wait
def _wait_with_readiness(self, run_id):
    ready = Path({str(ready)!r})
    release = Path({str(release)!r})
    if ready.exists():
        return _original_wait(self, run_id)
    original_read = self._run
    def first_read(selected_run):
        view = original_read(selected_run)
        if not ready.exists():
            ready.write_text(json.dumps({{
                "run_id": selected_run,
                "invocation_id": view["planes"]["main"]["active_invocation"],
            }}))
            end = _barrier_time.monotonic() + 30
            while not release.exists():
                if _barrier_time.monotonic() >= end:
                    raise RuntimeError("test idle readiness barrier expired")
                _barrier_time.sleep(0.01)
            return original_read(selected_run)
        return view
    self._run = first_read
    try:
        return _original_wait(self, run_id)
    finally:
        self._run = original_read
_Runtime._wait = _wait_with_readiness
'''

    def prepare(*args, **kwargs):
        result = original_init(*args, **kwargs)
        script = Path(args[1]) / "scripts" / "step.py"
        raw = script.read_text()
        marker = 'if __name__ == "__main__":'
        assert raw.count(marker) == 1
        script.write_text(raw.replace(marker, wrapper + "\n" + marker))
        return result

    monkeypatch.setattr(catalog, "init_template", prepare)
    return ready, release


def initialized(tmp_path: Path, **changes: object) -> dict[str, object]:
    """Real target, peer, frozen config and `initialize` through public Plus."""
    from millrace_plus.cicd_runtime import initialize

    target, head = make_target(tmp_path)
    peer = peer_executable(tmp_path)
    data = config(target, peer, **changes)
    config_file = write_json(tmp_path / "config.json", data)
    home, state = tmp_path / "home", tmp_path / "state"
    result = initialize(home, target, config_file, state, catalog_home=tmp_path / "cat")
    return {
        "root": tmp_path,
        "target": target,
        "head": head,
        "peer": peer,
        "config": config_file,
        "home": home,
        "state": state,
        "instance": result["instance_id"],
        "init": result,
    }
