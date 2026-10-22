"""Inert native peer: supplied local bytes and foreground child processes only."""

import base64
import json
import os
import signal  # noqa: F401 - used by supplied inert fixture snippets
import subprocess  # noqa: F401 - used by supplied inert fixture snippets
import sys
import time  # noqa: F401 - used by supplied inert fixture snippets
from pathlib import Path

options = json.loads(Path(sys.argv[0] + ".json").read_text())
if "--version" in sys.argv:
    os.write(1, base64.b64decode(options["version"]))
    sys.exit(options.get("version_exit", 0))
exec(options.get("before", ""))
prompt = sys.stdin.buffer.read()
if options.get("capture"):
    Path(options["capture"]).write_text(
        json.dumps(
            {
                "argv": sys.argv[1:],
                "prompt_bytes": len(prompt),
                "api": os.environ.get("ANTHROPIC_API_KEY"),
                "native": os.environ.get("MILLRACE_NATIVE_SENTINEL"),
                "path": os.environ.get("PATH"),
            }
        )
    )
os.write(2, base64.b64decode(options.get("stderr", "")))
os.write(1, base64.b64decode(options["wire"]))
exec(options.get("after", ""))
sys.exit(options.get("exit_code", 0))
