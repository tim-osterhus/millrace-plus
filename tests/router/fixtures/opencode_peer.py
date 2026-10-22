"""Inert pinned-wire peer. Configuration is test-owned; no native runtime runs."""

import base64
import json
import os
import signal  # noqa: F401 - test-owned exec hooks use these imports
import subprocess  # noqa: F401
import sys
import time  # noqa: F401
from pathlib import Path

options = json.loads(Path(sys.argv[0] + ".json").read_text())
if sys.argv[1:] == ["--version"]:
    exec(options.get("probe", ""))
    os.write(1, base64.b64decode(options["version"]))
    sys.exit(options.get("probe_exit", 0))
exec(options.get("before", ""))
prompt = sys.stdin.buffer.read()
if options.get("capture"):
    Path(options["capture"]).write_text(
        json.dumps(
            {
                "argv": sys.argv[1:],
                "stdin": base64.b64encode(prompt).decode(),
                "cwd": os.getcwd(),
                "environment": dict(os.environ),
            }
        )
    )
if "expected_prompt" in options:
    assert prompt == base64.b64decode(options["expected_prompt"])
if "expected_argv" in options:
    assert sys.argv[1:] == options["expected_argv"]
os.write(2, base64.b64decode(options["stderr"]))
os.write(1, base64.b64decode(options["wire"]))
exec(options.get("after", ""))
sys.exit(options.get("exit_code", 0))
