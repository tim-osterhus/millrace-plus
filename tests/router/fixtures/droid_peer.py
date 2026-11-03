"""Ordinary offline Droid version/result peer; never imports a native harness."""

import base64
import json
import os
import signal  # noqa: F401 - test-owned lifecycle hooks
import subprocess  # noqa: F401
import sys
import time  # noqa: F401
import uuid
from pathlib import Path


def main():
    options = json.loads(Path(sys.argv[0] + ".json").read_text())
    if options.get("starts"):
        with Path(options["starts"]).open("a") as log:
            log.write(json.dumps(sys.argv[1:]) + "\n")
    if sys.argv[1:] == ["--version"]:
        exec(options.get("probe", ""))
        os.write(2, base64.b64decode(options.get("probe_stderr", "")))
        os.write(1, base64.b64decode(options["version"]))
        return options.get("probe_exit", 0)
    assert sys.argv[1] == "exec"
    assert sys.argv[2] == "-f" and sys.argv[4] == "--cwd"
    assert sys.argv[6:8] == ["-o", "json"]
    assert sys.argv[5] == os.getcwd()
    assert sys.stdin.buffer.read() == b""
    prompt = Path(sys.argv[3]).read_bytes()
    if options.get("capture"):
        Path(options["capture"]).write_text(json.dumps({
            "argv": sys.argv[1:], "prompt": base64.b64encode(prompt).decode(),
            "cwd": os.getcwd(), "environment": dict(os.environ),
        }))
    exec(options.get("before", ""))
    os.write(2, base64.b64decode(options.get("stderr", "")))
    wire = base64.b64decode(options["wire"])
    if options.get("fresh_session"):
        result = json.loads(wire)
        result["session_id"] = uuid.uuid4().hex
        wire = json.dumps(result).encode()
    os.write(1, wire)
    exec(options.get("after", ""))
    return options.get("exit_code", 0)


if __name__ == "__main__":
    sys.exit(main())
