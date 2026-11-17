"""Millrace Forge runner worker for contract ``millrace-forge-runner/v1``.

Millrace Plus ships this file as package data and never imports it. Core writes
a copy as ``forge-worker.py`` under the invocation root. The parent adapter runs

    EXECUTABLE -I -B -X utf8 forge-worker.py forge-request.json

under a CPython >= 3.12 interpreter whose isolated environment holds
``forge-guardrails==0.10.1``. The start record arrives on stdin.

Rules (see the accepted worker contract and its R2B clarifications):

- Imports only the standard library and ``forge``. The source must parse under
  Python 3.11 grammar.
- The first action moves stdout to stderr. The one envelope line is the only
  write to the saved stdout descriptor. Exit 0 means ``completed``; exit 3
  means ``failed``.
- No exception text, endpoint or credential value reaches any output.
- Three synchronous tools: ``list_dir``, ``read_file`` and the terminal
  ``finish``. ``finish`` succeeds only as the sole call of its executed batch.
- No signal handler and no ``cancel_event``. The parent owns cancellation and
  the process deadline. This worker owns only the soft deadline.
- Writes no files. This is not a sandbox.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import stat
import sys
import time
from importlib import metadata
from urllib.parse import urlsplit

REQUEST_FORMAT = "millrace-forge-runner-request/v1"
START_FORMAT = "millrace-forge-runner-start/v1"
ENVELOPE_FORMAT = "millrace-forge-runner-worker/v1"
FORGE_VERSION = "0.10.1"
REQUEST_MAX = 1032192
START_MAX = 4096
ENVELOPE_MAX = 65537
SEMANTIC_MAX = 65536
SUMMARY_MAX = 16384
REFS_MAX = 32
REF_MAX = 512
PATH_MAX = 1024
OUTCOMES_MAX = 64
URL_MAX = 4096
ACLOSE_MAX = 2.0
BUDGETS = {
    "max_iterations": (1, 32),
    "max_retries_per_step": (0, 3),
    "max_tool_errors": (0, 3),
    "http_timeout_seconds": (1, 600),
    "tool_calls_max": (1, 128),
    "read_bytes_per_call": (1024, 65536),
    "tool_output_bytes_total": (65536, 4194304),
    "list_entries_max": (16, 1024),
}
WORKER_CODES = frozenset(
    {
        "forge_request_invalid",
        "forge_runtime_mismatch",
        "forge_workspace_mismatch",
        "forge_backend_config_invalid",
        "forge_backend_timeout",
        "forge_backend_http_error",
        "forge_backend_protocol",
        "forge_backend_transport",
        "forge_tool_call_exhausted",
        "forge_tool_errors_exhausted",
        "forge_max_iterations",
        "forge_context_budget_exceeded",
        "forge_step_enforcement",
        "forge_sdk_cancelled",
        "forge_sdk_error",
        "forge_deadline_exceeded",
        "forge_terminal_batch_violation",
        "forge_terminal_mismatch",
        "forge_result_too_large",
        "forge_worker_internal",
    }
)
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
HEX64 = re.compile(r"[0-9a-f]{64}")
DENIED = frozenset({".git", ".millrace-router-owner"})
# The SDK renders this through str.format, so it must contain no braces.
SYSTEM_PROMPT = (
    "You are a read-only assistant working inside one project workspace. "
    "Use list_dir and read_file to inspect files. Paths are relative to the "
    "workspace root. You cannot write files, run commands or use the network. "
    "When the task is done, call finish alone, as the only tool call in your "
    "response, with one declared outcome, a short summary and the relative "
    "paths you relied on."
)


class _Failure(Exception):
    """A worker-owned failure code. It never carries other text."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


# ---------------------------------------------------------------- input


def _strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def constant(_value):
        raise ValueError("non-finite number")

    return json.loads(
        raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant
    )


def _exact(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise _Failure("forge_request_invalid")
    return value


def _text(value, *, empty=False):
    if type(value) is not str or "\x00" in value or (not value and not empty):
        raise _Failure("forge_request_invalid")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise _Failure("forge_request_invalid") from exc
    return value


def _env_name(value):
    name = _text(value)
    if (
        not ENV_NAME.fullmatch(name)
        or name in {"PATH", "MILLRACE_SEAM_INPUT"}
        or name.startswith(("MILLRACE_", "PYTHON"))
    ):
        raise _Failure("forge_request_invalid")
    return name


def _read_start(raw):
    if len(raw) > START_MAX:
        raise _Failure("forge_request_invalid")
    try:
        obj = _strict_json(raw)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise _Failure("forge_request_invalid") from exc
    _exact(
        obj, ("format", "invocation_id", "process_deadline_unix", "soft_deadline_unix")
    )
    hard, soft = obj["process_deadline_unix"], obj["soft_deadline_unix"]
    if (
        obj["format"] != START_FORMAT
        or type(hard) is not float
        or type(soft) is not float
        or not math.isfinite(hard)
        or not math.isfinite(soft)
        or abs((hard - 3.0) - soft) > 1e-6
    ):
        raise _Failure("forge_request_invalid")
    _text(obj["invocation_id"])
    return obj


def _read_request(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        raise _Failure("forge_request_invalid") from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise _Failure("forge_request_invalid")
        chunks = []
        size = 0
        while size <= REQUEST_MAX:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
    except OSError as exc:
        raise _Failure("forge_request_invalid") from exc
    finally:
        os.close(fd)
    raw = b"".join(chunks)
    if len(raw) > REQUEST_MAX:
        raise _Failure("forge_request_invalid")
    try:
        obj = _strict_json(raw)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise _Failure("forge_request_invalid") from exc
    _exact(
        obj,
        (
            "format",
            "invocation_id",
            "plane_id",
            "node_id",
            "binding_id",
            "producing_revision",
            "workspace",
            "forge_version",
            "python_min",
            "backend",
            "budgets",
            "outcomes",
            "task_prompt",
            "task_prompt_sha256",
        ),
    )
    for key in (
        "invocation_id",
        "plane_id",
        "node_id",
        "binding_id",
        "producing_revision",
        "workspace",
    ):
        _text(obj[key])
    if (
        obj["format"] != REQUEST_FORMAT
        or obj["forge_version"] != FORGE_VERSION
        or obj["python_min"] != [3, 12]
        or not obj["workspace"].startswith("/")
    ):
        raise _Failure("forge_request_invalid")
    backend = _exact(obj["backend"], ("kind", "base_url_env", "auth", "model"))
    auth = backend["auth"]
    if backend["kind"] != "openai_compat" or type(auth) is not dict:
        raise _Failure("forge_request_invalid")
    url_name = _env_name(backend["base_url_env"])
    if auth.get("kind") == "env":
        _exact(auth, ("kind", "name"))
        if _env_name(auth["name"]) == url_name:
            raise _Failure("forge_request_invalid")
    elif auth != {"kind": "none"}:
        raise _Failure("forge_request_invalid")
    _text(backend["model"])
    budgets = _exact(obj["budgets"], tuple(BUDGETS))
    for key, (low, high) in BUDGETS.items():
        value = budgets[key]
        if type(value) is not int or not low <= value <= high:
            raise _Failure("forge_request_invalid")
    outcomes = obj["outcomes"]
    if (
        type(outcomes) is not list
        or not 1 <= len(outcomes) <= OUTCOMES_MAX
        or len(set(map(str, outcomes))) != len(outcomes)
    ):
        raise _Failure("forge_request_invalid")
    for item in outcomes:
        _text(item)
    prompt = _text(obj["task_prompt"])
    digest = obj["task_prompt_sha256"]
    if (
        type(digest) is not str
        or not HEX64.fullmatch(digest)
        or hashlib.sha256(prompt.encode("utf-8")).hexdigest() != digest
    ):
        raise _Failure("forge_request_invalid")
    return obj


def _check_runtime():
    """W-RT-05, before ``forge`` is imported."""
    try:
        version = metadata.version("forge-guardrails")
    except Exception as exc:
        raise _Failure("forge_runtime_mismatch") from exc
    if sys.version_info < (3, 12) or version != FORGE_VERSION:
        raise _Failure("forge_runtime_mismatch")


def _open_workspace(path):
    """Open the workspace once and require it to be the working directory."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except OSError as exc:
        raise _Failure("forge_workspace_mismatch") from exc
    try:
        opened, here = os.fstat(fd), os.stat(".")
        same = (opened.st_dev, opened.st_ino) == (here.st_dev, here.st_ino)
        same = same and os.path.realpath(os.getcwd()) == os.path.realpath(path)
    except OSError:
        same = False
    if not same:
        os.close(fd)
        raise _Failure("forge_workspace_mismatch")
    return fd


def _backend_values(backend):
    """Read the two referenced values at the execute boundary only."""
    url = os.environ.get(backend["base_url_env"])
    try:
        if (
            type(url) is not str
            or not url
            or len(url) > URL_MAX
            or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in url)
            or "?" in url
            or "#" in url
        ):
            raise ValueError("invalid")
        parts = urlsplit(url)
        _ = parts.port  # Raises ValueError for an invalid port.
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or "@" in parts.netloc
            or parts.username is not None
            or parts.password is not None
            or parts.query
            or parts.fragment
        ):
            raise ValueError("invalid")
    except ValueError as exc:
        raise _Failure("forge_backend_config_invalid") from exc
    key = ""
    if backend["auth"]["kind"] == "env":
        key = os.environ.get(backend["auth"]["name"])
        if type(key) is not str or not key or any(ch in key for ch in "\x00\r\n"):
            raise _Failure("forge_backend_config_invalid")
    return url, key


# ---------------------------------------------------------------- output


def _envelope(invocation_id, status, semantic, code):
    line = json.dumps(
        {
            "format": ENVELOPE_FORMAT,
            "invocation_id": invocation_id,
            "status": status,
            "semantic": semantic,
            "failure_code": code,
        },
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return (line + "\n").encode("utf-8")


def _emit(out, invocation_id, code, semantic=None):
    """Write the one envelope and return the exit code."""
    if code is None:
        raw = _envelope(invocation_id, "completed", semantic, None)
        if len(raw) <= ENVELOPE_MAX:
            _write(out, raw)
            return 0
        code = "forge_result_too_large"
    if code not in WORKER_CODES:
        code = "forge_worker_internal"
    _write(out, _envelope(invocation_id, "failed", None, code))
    return 3


def _write(fd, raw):
    view = memoryview(raw)
    while view:
        view = view[os.write(fd, view) :]


# ---------------------------------------------------------------- tools


def _path_parts(path):
    """Validate a tool path. Returns its components ([] for the root)."""
    if type(path) is not str:
        raise ValueError("path must be a string")
    if path in {"", "."}:
        return []
    try:
        size = len(path.encode("utf-8"))
    except UnicodeError:
        size = PATH_MAX + 1
    parts = path.split("/")
    if (
        size > PATH_MAX
        or "\x00" in path
        or path.startswith("/")
        or any(part in {"", ".", ".."} for part in parts)
        or any(part.casefold() in DENIED for part in parts)
    ):
        return None
    return parts


def _ref_valid(ref):
    if type(ref) is not str or "\x00" in ref:
        return False
    try:
        size = len(ref.encode("utf-8"))
    except UnicodeError:
        return False
    parts = ref.split("/")
    return (
        1 <= size <= REF_MAX
        and not ref.startswith("/")
        and all(part and part != ".." for part in parts)
        and all(part.casefold() != ".millrace-router-owner" for part in parts)
    )


class _Tools:
    """Worker-owned tool state, call log and batch observer."""

    def __init__(self, request, root_fd, errors, message_type):
        self.invocation_id = request["invocation_id"]
        self.budgets = request["budgets"]
        self.outcomes = tuple(request["outcomes"])
        self.root = root_fd
        self.resolution = errors.ToolResolutionError
        self.tool_call = message_type.TOOL_CALL
        self.calls = 0
        self.output = 0
        self.batch = None
        self.inconsistent = False
        self.log = []
        self.finished = None
        self.finished_batch = None

    # The observer only records. It never raises or changes a message (W-TB-04).
    def observe(self, message):
        try:
            if message.metadata.type == self.tool_call:
                names = tuple(call.name for call in message.tool_calls)
                if not names or any(type(name) is not str for name in names):
                    raise ValueError("unreadable batch")
                self.batch = names
        except Exception:
            self.inconsistent = True

    def _refuse(self, name, text):
        self.log.append((name, "resolution"))
        raise self.resolution(name + " refused: " + text)

    def _invalid(self, name, text):
        self.log.append((name, "argument"))
        raise ValueError(name + " arguments invalid: " + text)

    def _budget(self, name):
        if (
            self.calls >= self.budgets["tool_calls_max"]
            or self.output >= self.budgets["tool_output_bytes_total"]
        ):
            self._refuse(name, "read budget exhausted")
        self.calls += 1

    def _account(self, name, text):
        size = len(text.encode("utf-8"))
        if self.output + size > self.budgets["tool_output_bytes_total"]:
            self.output = self.budgets["tool_output_bytes_total"]
            self._refuse(name, "read budget exhausted")
        self.output += size
        self.log.append((name, "ok"))
        return text

    def _walk(self, parts, want_dir):
        """Open the target relative to the workspace fd without following links."""
        current = os.dup(self.root)
        try:
            for index, part in enumerate(parts):
                final_file = index == len(parts) - 1 and not want_dir
                before = os.stat(part, dir_fd=current, follow_symlinks=False)
                kind = stat.S_IFMT(before.st_mode)
                if kind != (stat.S_IFREG if final_file else stat.S_IFDIR):
                    raise LookupError("wrong type")
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
                flags |= os.O_NONBLOCK if final_file else os.O_DIRECTORY
                opened = os.open(part, flags, dir_fd=current)
                os.close(current)
                current = opened
                after = os.fstat(current)
                if (after.st_dev, after.st_ino) != (
                    before.st_dev,
                    before.st_ino,
                ) or stat.S_IFMT(after.st_mode) != kind:
                    raise LookupError("identity changed")
            return current
        except BaseException:
            os.close(current)
            raise

    def list_dir(self, **args):
        try:
            if set(args) != {"path"}:
                raise ValueError("expected exactly path")
            parts = _path_parts(args["path"])
        except ValueError:
            self._invalid("list_dir", "expected one string path")
        if parts is None:
            self._refuse("list_dir", "path denied")
        self._budget("list_dir")
        try:
            fd = self._walk(parts, True)
            try:
                names = os.listdir(fd)
                entries = []
                undecodable = 0
                for name in names:
                    try:
                        encoded = name.encode("utf-8")
                    except UnicodeError:
                        undecodable += 1
                        continue
                    if name.casefold() in DENIED:
                        continue
                    try:
                        mode = os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode
                        kind = (
                            "file"
                            if stat.S_ISREG(mode)
                            else "dir"
                            if stat.S_ISDIR(mode)
                            else "symlink"
                            if stat.S_ISLNK(mode)
                            else "other"
                        )
                    except OSError:
                        kind = "other"
                    entries.append((encoded, name, kind))
            finally:
                os.close(fd)
        except Exception:
            self._refuse("list_dir", "path unavailable")
        entries.sort()
        limit = self.budgets["list_entries_max"]
        text = json.dumps(
            {
                "path": "/".join(parts),
                "entries": [
                    {"name": name, "type": kind} for _, name, kind in entries[:limit]
                ],
                "truncated": len(entries) > limit,
                "omitted_undecodable": undecodable,
            },
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return self._account("list_dir", text)

    def read_file(self, **args):
        window = self.budgets["read_bytes_per_call"]
        try:
            if "path" not in args or set(args) - {"path", "offset", "length"}:
                raise ValueError("unexpected keys")
            offset, length = args.get("offset", 0), args.get("length", window)
            if (
                type(offset) is not int
                or type(length) is not int
                or offset < 0
                or not 1 <= length <= window
            ):
                raise ValueError("bad window")
            parts = _path_parts(args["path"])
        except ValueError:
            self._invalid("read_file", "expected path and integer offset/length")
        if not parts:
            self._refuse("read_file", "path denied")
        self._budget("read_file")
        try:
            fd = self._walk(parts, False)
            try:
                size = os.fstat(fd).st_size
                if offset > size:
                    raise LookupError("window outside file")
                data = os.pread(fd, length, offset)
            finally:
                os.close(fd)
            text = data.decode("utf-8")
        except Exception:
            self._refuse("read_file", "path unavailable or not UTF-8 text")
        result = json.dumps(
            {
                "path": "/".join(parts),
                "offset": offset,
                "length": len(data),
                "size": size,
                "eof": offset + len(data) >= size,
                "text": text,
            },
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return self._account("read_file", result)

    def finish(self, **args):
        try:
            if set(args) != {"outcome", "summary", "refs"}:
                raise ValueError("keys")
            outcome, summary, refs = args["outcome"], args["summary"], args["refs"]
            if (
                type(outcome) is not str
                or outcome not in self.outcomes
                or type(summary) is not str
                or "\x00" in summary
                or len(summary.encode("utf-8")) > SUMMARY_MAX
                or type(refs) is not list
                or len(refs) > REFS_MAX
                or not all(_ref_valid(ref) for ref in refs)
            ):
                raise ValueError("values")
            semantic = json.dumps(
                {"outcome": outcome, "summary": summary, "refs": refs},
                separators=(",", ":"),
                ensure_ascii=False,
            )
            if (
                len(semantic.encode("utf-8")) > SEMANTIC_MAX
                or len(_envelope(self.invocation_id, "completed", semantic, None))
                > ENVELOPE_MAX
            ):
                raise ValueError("too large")
        except (ValueError, TypeError, UnicodeError):
            self._invalid("finish", "expected a declared outcome, summary and refs")
        if self.batch != ("finish",):
            self._refuse("finish", "finish must be the only tool call in its response")
        self.log.append(("finish", "ok"))
        self.finished = semantic
        self.finished_batch = self.batch
        return semantic

    def post_run(self, result):
        """W-TB-03 checks after a normal, unexpired ``run()`` return."""
        if self.inconsistent:
            return "forge_worker_internal"
        finishes = [i for i, entry in enumerate(self.log) if entry == ("finish", "ok")]
        if (
            len(finishes) != 1
            or finishes[0] != len(self.log) - 1
            or self.finished_batch != ("finish",)
        ):
            return "forge_terminal_batch_violation"
        if result is not self.finished:
            return "forge_terminal_mismatch"
        return None


def _workflow(forge, tools):
    def spec(name, description, properties, required):
        return forge.ToolSpec.from_json_schema(
            name,
            description,
            {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        )

    definitions = (
        forge.ToolDef(
            spec=spec(
                "list_dir",
                "List one workspace directory. Symlinks are not followed.",
                {
                    "path": {
                        "type": "string",
                        "description": "Relative directory path; '' is the root.",
                    }
                },
                ["path"],
            ),
            callable=tools.list_dir,
        ),
        forge.ToolDef(
            spec=spec(
                "read_file",
                "Read a UTF-8 byte window of one regular workspace file.",
                {
                    "path": {"type": "string"},
                    "offset": {"type": "integer"},
                    "length": {"type": "integer"},
                },
                ["path"],
            ),
            callable=tools.read_file,
        ),
        forge.ToolDef(
            spec=spec(
                "finish",
                "Report the final result. Call it alone in its response.",
                {
                    "outcome": {"type": "string", "enum": list(tools.outcomes)},
                    "summary": {"type": "string"},
                    "refs": {"type": "array", "items": {"type": "string"}},
                },
                ["outcome", "summary", "refs"],
            ),
            callable=tools.finish,
        ),
    )
    return forge.Workflow(
        name="millrace_forge_runner",
        description="Read-only Millrace task",
        tools={item.name: item for item in definitions},
        required_steps=[],
        terminal_tool="finish",
        system_prompt_template=SYSTEM_PROMPT,
    )


# ---------------------------------------------------------------- errors


def _from_httpx(value, name):
    return any(
        cls.__name__ == name and str(cls.__module__).split(".")[0] == "httpx"
        for cls in type(value).__mro__
    )


def _classify(exc, cm, errors):
    """W-ER-01 by class and attributes only; never copies exception text."""
    try:
        if cm is not None and cm.expired():
            return "forge_deadline_exceeded"
        if isinstance(exc, asyncio.CancelledError):
            return "forge_worker_internal"
        for kinds, code in (
            ((errors.MaxIterationsError,), "forge_max_iterations"),
            ((errors.ToolCallError,), "forge_tool_call_exhausted"),
            ((errors.ToolExecutionError,), "forge_tool_errors_exhausted"),
            (
                (errors.StepEnforcementError, errors.PrerequisiteError),
                "forge_step_enforcement",
            ),
            ((errors.WorkflowCancelledError,), "forge_sdk_cancelled"),
            ((errors.ContextBudgetExceeded,), "forge_context_budget_exceeded"),
            (
                (errors.MultipleCredentialsError, errors.MissingCredentialError),
                "forge_backend_config_invalid",
            ),
        ):
            if isinstance(exc, kinds):
                return code
        if isinstance(exc, errors.BackendError):
            status = getattr(exc, "status_code", None)
            if status == 408 and _from_httpx(exc.__cause__, "ReadTimeout"):
                return "forge_backend_timeout"
            if type(status) is int and str(exc) == f"Backend returned {status}":
                return "forge_backend_http_error"
            return "forge_backend_protocol"
        if isinstance(exc, errors.ForgeError):
            return "forge_sdk_error"
        if _from_httpx(exc, "TransportError"):
            return "forge_backend_transport"
        if isinstance(exc, json.JSONDecodeError):
            return "forge_backend_protocol"
    except Exception:
        pass
    return "forge_worker_internal"


# ---------------------------------------------------------------- lifecycle


async def _aclose(client, hard_deadline):
    """Bounded client close (W-LC-05). Its outcome never changes the code."""
    bound = min(ACLOSE_MAX, hard_deadline - 0.5 - time.time())
    if bound <= 0:
        return
    try:
        async with asyncio.timeout(bound):
            await client.aclose()
    except (Exception, asyncio.CancelledError):
        # No external canceller exists in this process (W-LC-06). A pending
        # cancellation here can only come from this worker's own timeouts.
        pass


async def _session(out, request, start, client, runner, workflow, tools, errors):
    code, result, cm = None, None, None
    remaining = start["soft_deadline_unix"] - time.time()
    if remaining <= 0:
        code = "forge_deadline_exceeded"
    else:
        try:
            async with asyncio.timeout(remaining) as cm:
                result = await runner.run(workflow, request["task_prompt"])
        except (Exception, asyncio.CancelledError) as exc:
            # The worker is the top-level owner. Parent cancellation is SIGTERM,
            # so a CancelledError here is classified, not propagated (W-ER-01).
            code = _classify(exc, cm, errors)
        else:
            # C1: an expired timeout wins over every post-run code. A synchronous
            # overrun with no later await leaves cm unexpired, so the soft
            # deadline itself is also checked.
            expired = cm.expired() or time.time() >= start["soft_deadline_unix"]
            code = "forge_deadline_exceeded" if expired else tools.post_run(result)
    await _aclose(client, start["process_deadline_unix"])
    if code is None:
        return _emit(out, request["invocation_id"], None, tools.finished)
    return _emit(out, request["invocation_id"], code)


def _run(out, ids):
    start = request = failure = None
    try:
        if len(sys.argv) != 2:
            raise _Failure("forge_request_invalid")
        start = _read_start(sys.stdin.buffer.read(START_MAX + 1))
        ids.append(start["invocation_id"])
    except _Failure as exc:
        failure = exc
    try:
        if len(sys.argv) == 2:
            request = _read_request(sys.argv[1])
            ids.append(request["invocation_id"])
    except _Failure as exc:
        failure = failure or exc
    try:
        if failure is not None:
            raise failure
        if start["invocation_id"] != request["invocation_id"]:
            raise _Failure("forge_request_invalid")
        _check_runtime()
        root = _open_workspace(request["workspace"])
        url, key = _backend_values(request["backend"])
    except _Failure as exc:
        return _emit(out, ids[-1] if ids else "", exc.code)

    errors = None
    try:
        import forge
        import forge.errors as errors

        tools = _Tools(request, root, errors, forge.MessageType)
        budgets = request["budgets"]
        client = forge.OpenAICompatClient(
            request["backend"]["model"],
            url,
            api_key=key,
            timeout=float(budgets["http_timeout_seconds"]),
            recommended_sampling=False,
        )
        del url, key
        runner = forge.WorkflowRunner(
            client,
            forge.ContextManager(forge.NoCompact(), budget_tokens=None),
            max_iterations=budgets["max_iterations"],
            max_retries_per_step=budgets["max_retries_per_step"],
            max_tool_errors=budgets["max_tool_errors"],
            stream=False,
            on_message=tools.observe,
            rescue_enabled=False,
            reasoning_replay="none",
        )
        workflow = _workflow(forge, tools)
    except Exception as exc:
        code = _classify(exc, None, errors) if errors else "forge_worker_internal"
        return _emit(out, request["invocation_id"], code)
    return asyncio.run(
        _session(out, request, start, client, runner, workflow, tools, errors)
    )


def main():
    out = os.dup(1)  # Non-inheritable by default (PEP 446).
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    logging.disable(logging.CRITICAL)
    ids = []
    try:
        return _run(out, ids)
    except Exception:
        return _emit(out, ids[-1] if ids else "", "forge_worker_internal")


if __name__ == "__main__":
    sys.exit(main())
