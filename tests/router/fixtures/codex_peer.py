"""Credential-free fake for the exact finite Codex exec argv/stdin/file wire."""

from __future__ import annotations

import base64
import json
import os
import signal  # noqa: F401 - trusted test hook namespace
import subprocess  # noqa: F401 - trusted test hook namespace
import sys
import time  # noqa: F401 - trusted test hook namespace
from pathlib import Path

config = json.loads(Path(sys.argv[0] + ".json").read_text())
if "--version" in sys.argv:
    exec(config["version_extra"])
    print("codex-cli " + config["version"])
    sys.exit(0)
args = sys.argv[1:]
assert args[0] == "exec", args
expected = [
    "exec",
    "-c",
    'model_provider="openai"',
    "-c",
    'forced_login_method="chatgpt"',
    "-c",
    'cli_auth_credentials_store="file"',
    "-c",
    'approval_policy="never"',
    "-c",
    'model_reasoning_effort="low"',
    "--json",
    "--ephemeral",
    "--ignore-user-config",
    "--ignore-rules",
    "--skip-git-repo-check",
    "--color",
    "never",
    "--sandbox",
    "workspace-write",
    "--model",
    "exact-model",
    "--output-schema",
]
# Tests may deliberately change typed model/effort/sandbox settings.
expected[10], expected[19], expected[21] = args[10], args[19], args[21]
expected.extend((args[23], "--output-last-message", args[25], "-"))
assert args == expected, args
assert all(
    k
    in {
        "PATH",
        "CODEX_HOME",
        "MILLRACE_SEAM_INPUT",
        "HOME",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "TZ",
        "LC_CTYPE",
        "__CF_USER_TEXT_ENCODING",
    }
    for k in os.environ
), os.environ.keys()
assert Path(os.environ["CODEX_HOME"]).is_dir()
output = Path(args[args.index("--output-last-message") + 1])
schema = json.loads(Path(args[args.index("--output-schema") + 1]).read_text())
assert schema["required"] == ["outcome", "summary", "refs"]
exec(config["before"])
input_bytes = sys.stdin.buffer.read()
assert input_bytes.startswith(b"Millrace invocation instructions.")
if config["final"] is not None:
    output.write_bytes(base64.b64decode(config["final"]))
os.write(1, base64.b64decode(config["wire"]))
exec(config["after"])
sys.exit(config["exit_code"])
