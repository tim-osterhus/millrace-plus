"""Inert Prime text-print peer; no native harness or provider imports."""

import base64
import json
import os
import signal  # noqa: F401 - test-owned lifecycle hooks
import subprocess  # noqa: F401
import sys
import time  # noqa: F401
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
    assert sys.argv[1:4] == ["--print", "--no-session", "--cwd"]
    assert sys.argv[4] == os.getcwd()
    assert sys.argv[-2] == "--"
    assert all(arg in {"--provider", "--model", "--thinking"}
               for arg in sys.argv[5:-2:2])
    assert sys.stdin.buffer.read() == b""
    prompt = sys.argv[-1].encode("utf-8")
    if options.get("capture"):
        Path(options["capture"]).write_text(json.dumps({
            "argv": sys.argv[1:], "prompt": base64.b64encode(prompt).decode(),
            "cwd": os.getcwd(), "environment": dict(os.environ),
        }))
    exec(options.get("before", ""))
    os.write(2, base64.b64decode(options.get("stderr", "")))
    os.write(1, base64.b64decode(options["wire"]))
    exec(options.get("after", ""))
    return options.get("exit_code", 0)


if __name__ == "__main__":
    sys.exit(main())
