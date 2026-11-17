"""Worker cases: the real packaged ``forge_worker.py`` over a FAKE ``forge`` package.

These cases prove worker logic only. The fake in ``fixtures/forge_fake_sdk/``
reproduces the pinned runner facts the contract cites; it is not the Forge SDK.
Pinned-SDK proof (G05) is separate and not admitted. No case opens a network
connection: the fake client returns scripted responses.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
from forge_support import (
    ENV,
    FAKE_SDK,
    KEY,
    KEY_ENV,
    LOOPBACK,
    NORMAL_SOFT,
    PROFILE,
    URL_ENV,
    finish_call,
    run_worker,
    workspace,
)

from millrace_plus.router import forge_configuration as c

SECRET = b"SECRET"
READ_REFUSED = (
    "[ToolResolutionError] read_file refused: path unavailable or not UTF-8 text"
)
# 16,384 bytes (SUMMARY_MAX). Each quote is 2 bytes in the semantic string and 4
# bytes once that string is encoded again inside the envelope.
QUOTES = '"' * 16384


def code(worker) -> str | None:
    assert worker.envelope is not None, (worker.code, worker.stdout, worker.stderr)
    return worker.envelope["failure_code"]


def ok(worker) -> dict:
    assert worker.code == 0, (worker.stdout, worker.stderr)
    assert worker.envelope["status"] == "completed"
    return json.loads(worker.envelope["semantic"])


def results(worker) -> list[str]:
    return [
        e["content"]
        for e in worker.events
        if e["kind"] == "message" and e["type"] == "tool_result"
    ]


def batches(worker) -> list[list[str]]:
    return [
        e["calls"]
        for e in worker.events
        if e["kind"] == "message" and e["type"] == "tool_call"
    ]


def kinds(worker) -> list[str]:
    return [e["kind"] for e in worker.events]


def of(worker, kind: str) -> dict:
    return next(e for e in worker.events if e["kind"] == kind)


# ---------------------------------------------------------------- output channel


def test_stdout_isolation(tmp_path):
    worker = run_worker(
        tmp_path,
        {"import_print": "LIBRARY-PRINT", "responses": [{"calls": [finish_call()]}]},
    )
    assert ok(worker)["outcome"] == "READY"
    assert worker.stdout == c.envelope_line(
        worker.plan.invocation_id, "completed", worker.envelope["semantic"], None
    )
    assert b"LIBRARY-PRINT" in worker.stderr
    assert b"LIBRARY-PRINT" not in worker.stdout


@pytest.mark.parametrize(
    "kind",
    [
        "status_500",
        "no_choices",
        "transport",
        "forge_error",
        "timeout_error",
        "json_decode",
        "non_object",
    ],
)
def test_no_exception_text_output(tmp_path, kind):
    worker = run_worker(
        tmp_path,
        {
            "responses": [
                {"calls": [["read_file", {"path": "missing/file.txt"}]]},
                {"raise": kind},
            ]
        },
    )
    assert worker.code == 3 and worker.envelope["status"] == "failed"
    assert SECRET not in worker.stdout and SECRET not in worker.stderr
    assert b"Traceback" not in worker.stderr
    for text in results(worker):
        assert "Errno" not in text and "No such file" not in text
        assert str(tmp_path) not in text


# ---------------------------------------------------------------- runtime and setup


def test_runtime_mismatch_before_import(tmp_path):
    worker = run_worker(tmp_path, {"responses": []}, python=(3, 11, 9))
    assert code(worker) == "forge_runtime_mismatch" and worker.code == 3
    assert "imported" not in kinds(worker)
    other = tmp_path / "other-sdk"
    shutil.copytree(FAKE_SDK, other)
    meta = other / "forge_guardrails-0.10.1.dist-info" / "METADATA"
    meta.write_text(meta.read_text().replace("Version: 0.10.1", "Version: 0.10.0"))
    worker = run_worker(tmp_path / "wrong", {"responses": []}, fake_sdk=str(other))
    assert code(worker) == "forge_runtime_mismatch"
    assert "imported" not in kinds(worker)


def test_fixed_sdk_configuration(tmp_path):
    prompt = b"Inspect {braces} verbatim.\n"
    worker = run_worker(
        tmp_path, {"responses": [{"calls": [finish_call()]}]}, prompt=prompt
    )
    ok(worker)
    # The exact-name fake finder loaded the owned files; no search path was added.
    imported = of(worker, "imported")
    assert imported["file"] == str((FAKE_SDK / "forge" / "__init__.py").resolve())
    assert imported["errors_file"] == str((FAKE_SDK / "forge" / "errors.py").resolve())
    assert imported["root_on_search_path"] is False
    client = of(worker, "client")
    assert {
        k: client[k]
        for k in (
            "model",
            "base_url",
            "api_key_sent",
            "timeout",
            "recommended_sampling",
            "extra",
        )
    } == {
        "model": "fixture-model",
        "base_url": LOOPBACK,
        "api_key_sent": True,
        "timeout": 120.0,
        "recommended_sampling": False,
        "extra": [],
    }
    assert of(worker, "context") == {
        "kind": "context",
        "strategy": "NoCompact",
        "budget_tokens": None,
        "extra": [],
    }
    runner = of(worker, "runner")
    assert {k: v for k, v in runner.items() if k != "kind"} == {
        "max_iterations": 16,
        "max_retries_per_step": 2,
        "max_tool_errors": 2,
        "stream": False,
        "on_chunk": False,
        "on_message": True,
        "rescue_enabled": False,
        "retry_nudge": False,
        "reasoning_replay": "none",
    }
    run = of(worker, "run")
    assert run["user_message"] == prompt.decode()
    assert run["prompt_vars"] is None and run["initial_messages"] is False
    assert run["cancel_event"] is False and run["required_steps"] == []
    assert run["terminal_tools"] == ["finish"]
    assert "{" not in run["system_prompt"] and "}" not in run["system_prompt"]
    assert set(run["tools"]) == {"list_dir", "read_file", "finish"}
    assert run["tools"]["finish"]["properties"]["outcome"]["enum"] == ["READY"]


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "relative/v1",
        "ftp://127.0.0.1/v1",
        "http://",
        "http:///v1",
        "http://user:pw@127.0.0.1/v1",
        "http://127.0.0.1/v1?x=1",
        "http://127.0.0.1/v1#frag",
        "http://127.0.0.1:notaport/v1",
        "http://127.0.0.1/ v1",
        "http://127.0.0.1/v1\n",
    ],
)
def test_backend_reference_refusals(tmp_path, value):
    env = {k: v for k, v in ENV.items() if k != URL_ENV}
    env["OPENAI_BASE_URL"] = LOOPBACK  # A would-be fallback variable is never read.
    if value is not None:
        env[URL_ENV] = value
    worker = run_worker(tmp_path, {"responses": [{"calls": [finish_call()]}]}, env=env)
    assert code(worker) == "forge_backend_config_invalid"
    assert not value or value.encode() not in worker.stdout + worker.stderr


def test_backend_auth_reference(tmp_path):
    for env in ({k: v for k, v in ENV.items() if k != KEY_ENV}, {**ENV, KEY_ENV: ""}):
        worker = run_worker(tmp_path / str(len(env)), {"responses": []}, env=env)
        assert code(worker) == "forge_backend_config_invalid"
    none = {**PROFILE, "auth": {"kind": "none"}}
    worker = run_worker(
        tmp_path / "none",
        {"responses": [{"calls": [finish_call()]}]},
        profile=none,
        env={k: v for k, v in ENV.items() if k != KEY_ENV},
    )
    ok(worker)
    assert of(worker, "client")["api_key_sent"] is False
    worker = run_worker(tmp_path / "env", {"responses": [{"calls": [finish_call()]}]})
    assert of(worker, "client")["api_key"] == KEY
    assert KEY.encode() not in worker.stdout and KEY.encode() not in worker.stderr


def test_request_and_start_record_refusals(tmp_path):
    worker = run_worker(tmp_path / "a", {"responses": []}, start=b"{}")
    assert code(worker) == "forge_request_invalid"
    worker = run_worker(
        tmp_path / "b", {"responses": []}, start=c.start_record("f" * 32, 2e9, 2e9 - 3)
    )
    assert code(worker) == "forge_request_invalid"
    worker = run_worker(
        tmp_path / "c", {"responses": []}, start=c.start_record("1" * 32, 2e9, 2e9 - 2)
    )
    assert code(worker) == "forge_request_invalid"
    worker = run_worker(tmp_path / "d", {"responses": []}, cwd=tmp_path)
    assert code(worker) == "forge_workspace_mismatch"


def tree(root: Path) -> dict[str, tuple[int, int]]:
    return {
        str(p.relative_to(root)): (p.lstat().st_size, p.lstat().st_mtime_ns)
        for p in sorted(root.rglob("*"))
    }


def test_no_writes(tmp_path):
    ws = workspace(tmp_path)
    (ws / "data.txt").write_text("hello")
    (ws / "sub").mkdir()
    before = tree(ws)
    worker = run_worker(
        tmp_path,
        {
            "responses": [
                {
                    "calls": [
                        ["list_dir", {"path": ""}],
                        ["read_file", {"path": "data.txt"}],
                    ]
                },
                {"calls": [finish_call(refs=["data.txt"])]},
            ]
        },
    )
    assert ok(worker)["refs"] == ["data.txt"]
    assert tree(ws) == before
    inv = tmp_path / "inv"
    assert not list(inv.rglob("__pycache__")) and not list(ws.rglob("__pycache__"))
    assert sorted(p.name for p in inv.iterdir()) == sorted(
        [c.PROBE_NAME, c.REQUEST_NAME, c.WORKER_NAME]
    )


# ---------------------------------------------------------------- terminal policy


@pytest.mark.parametrize("order", ["finish_first", "finish_last"])
def test_finish_must_be_sole_call(tmp_path, order):
    pair = [finish_call(), ["list_dir", {"path": ""}]]
    if order == "finish_last":
        pair.reverse()
    worker = run_worker(
        tmp_path, {"responses": [{"calls": pair}, {"calls": [finish_call()]}]}
    )
    ok(worker)
    first = results(worker)[:2]
    refused = [text for text in first if text.startswith("[ToolResolutionError]")]
    assert refused == [
        "[ToolResolutionError] finish refused: finish must be the only "
        "tool call in its response"
    ]
    assert any(text.startswith('{"path":""') for text in first)  # Sibling ran.
    assert batches(worker) == [[n for n, _ in pair], ["finish"]]


def test_finish_with_unknown_tool_executes_nothing(tmp_path):
    worker = run_worker(
        tmp_path,
        {
            "responses": [
                {"calls": [finish_call(), ["shell", {"cmd": "id"}]]},
                {"calls": [finish_call()]},
            ]
        },
    )
    ok(worker)
    assert len(results(worker)) == 1  # Only the later sole finish executed.
    assert batches(worker)[-1] == ["finish"]


@pytest.mark.parametrize(
    "sibling",
    [
        ["read_file", {"path": "x", "offset": True}],
        ["read_file", {"path": "missing.txt"}],
    ],
)
def test_finish_with_failing_sibling(tmp_path, sibling):
    worker = run_worker(
        tmp_path,
        {
            "responses": [
                {"calls": [sibling, finish_call()]},
                {"calls": [finish_call()]},
            ]
        },
    )
    ok(worker)
    assert results(worker)[1].startswith("[ToolResolutionError] finish refused")


def test_observer_tracks_executed_batch(tmp_path):
    worker = run_worker(
        tmp_path,
        {
            "responses": [
                {"text": "thinking out loud"},
                {"calls": [finish_call(), ["bogus", {}]]},
                {"calls": [finish_call()]},
            ]
        },
    )
    ok(worker)
    assert batches(worker)[-1] == ["finish"]
    unreadable = run_worker(
        tmp_path / "unreadable",
        {
            "malformed_tool_call": True,
            "responses": [{"calls": [finish_call()]}],
        },
    )
    assert "observer_returned" in kinds(unreadable)  # The callback never raised.
    assert code(unreadable) == "forge_worker_internal"
    assert b"Traceback" not in unreadable.stderr and b"TypeError" not in (
        unreadable.stdout + unreadable.stderr
    )


def test_double_finish_then_sole_finish(tmp_path):
    worker = run_worker(
        tmp_path,
        {
            "responses": [
                {"calls": [finish_call(), finish_call(summary="second")]},
                {"calls": [finish_call(summary="final")]},
            ]
        },
    )
    assert ok(worker)["summary"] == "final"
    assert all(text.startswith("[ToolResolutionError]") for text in results(worker)[:2])


def test_finish_errors_and_earlier_failures(tmp_path):
    worker = run_worker(
        tmp_path,
        {
            "responses": [
                {"calls": [finish_call(outcome="UNDECLARED")]},
                {"calls": [["read_file", {"path": "missing.txt"}]]},
                {"calls": [finish_call()]},
            ]
        },
    )
    ok(worker)
    assert results(worker)[0] == (
        "[ToolError] ValueError: finish arguments invalid: expected a declared "
        "outcome, summary and refs"
    )
    worker = run_worker(
        tmp_path / "exhausted",
        {
            "responses": [
                {"calls": [finish_call(outcome="UNDECLARED")]} for _ in range(3)
            ]
        },
    )
    assert code(worker) == "forge_tool_errors_exhausted"


@pytest.mark.parametrize(
    "scenario,expected",
    [
        ({"after_terminal": {"other_object": True}}, "forge_terminal_mismatch"),
        ({"after_terminal": {"extra_call": True}}, "forge_terminal_batch_violation"),
        ({"return_without_finish": "{}"}, "forge_terminal_batch_violation"),
        # C1: an expired timeout after a normal async-with exit wins over everything.
        (
            {"after_terminal": {"block": 3.0, "swallow_cancel": 0.5}},
            "forge_deadline_exceeded",
        ),
        # C1 precedence: expiry together with an invalid terminal record.
        (
            {
                "after_terminal": {
                    "block": 3.0,
                    "swallow_cancel": 0.5,
                    "other_object": True,
                }
            },
            "forge_deadline_exceeded",
        ),
        (
            {
                "after_terminal": {
                    "block": 3.0,
                    "swallow_cancel": 0.5,
                    "extra_call": True,
                }
            },
            "forge_deadline_exceeded",
        ),
        # Synchronous overrun with no later await: the soft-deadline check fails closed.
        ({"after_terminal": {"block": 3.0}}, "forge_deadline_exceeded"),
    ],
)
def test_completed_only_after_post_run_checks(tmp_path, scenario, expected):
    # N4: non-expiry cases get the normal allowance; expiry cases stay short.
    expiring = expected == "forge_deadline_exceeded"
    worker = run_worker(
        tmp_path,
        {"responses": [{"calls": [finish_call()]}], **scenario},
        soft_in=2.0 if expiring else NORMAL_SOFT,
    )
    assert code(worker) == expected
    assert worker.code == 3 and worker.envelope["semantic"] is None


# ---------------------------------------------------------------- error mapping


@pytest.mark.parametrize(
    "step,expected",
    [
        ({"raise": "read_timeout_408"}, "forge_backend_timeout"),
        ({"raise": "status_408"}, "forge_backend_http_error"),
        ({"raise": "status_500"}, "forge_backend_http_error"),
        ({"raise": "no_choices"}, "forge_backend_protocol"),
        ({"raise": "transport"}, "forge_backend_transport"),
        ({"raise": "json_decode"}, "forge_backend_protocol"),
        ({"raise": "forge_error"}, "forge_sdk_error"),
        ({"raise": "missing_credential"}, "forge_backend_config_invalid"),
        ({"raise": "max_iterations"}, "forge_max_iterations"),
        ({"raise": "context_budget"}, "forge_context_budget_exceeded"),
        ({"raise": "step_enforcement"}, "forge_step_enforcement"),
        ({"raise": "sdk_cancelled"}, "forge_sdk_cancelled"),
        ({"raise": "timeout_error"}, "forge_worker_internal"),
        ({"raise": "non_object"}, "forge_worker_internal"),
    ],
)
def test_error_taxonomy_order(tmp_path, step, expected):
    assert code(run_worker(tmp_path, {"responses": [step]})) == expected


@pytest.mark.parametrize(
    "step",
    [
        {"text": "plain text"},
        {"calls": [["read_file", "not-a-dict"]]},
        {"calls": [["unknown_tool", {}]]},
    ],
)
def test_tool_call_budget_exhaustion(tmp_path, step):
    worker = run_worker(tmp_path, {"responses": [step] * 4})
    assert code(worker) == "forge_tool_call_exhausted"


def test_expired_context_classified_first(tmp_path):
    for step in ({"sleep": 30, "convert_cancel": "status_500"}, {"sleep": 30}):
        worker = run_worker(
            tmp_path / str(len(step)), {"responses": [step]}, soft_in=1.5
        )
        assert code(worker) == "forge_deadline_exceeded"
        assert worker.elapsed < 15
    worker = run_worker(tmp_path / "raw", {"responses": [{"raise": "cancelled"}]})
    assert code(worker) == "forge_worker_internal"


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("json_decode", "forge_backend_protocol"),
        ("non_object", "forge_worker_internal"),
        ("int_limit", "forge_worker_internal"),
        ("wrong_choices", "forge_worker_internal"),
        ("wrong_usage", "forge_worker_internal"),
        ("argument_int_limit", "forge_worker_internal"),
    ],
)
def test_malformed_backend_and_argument_bodies(tmp_path, kind, expected):
    worker = run_worker(tmp_path, {"responses": [{"raise": kind}]})
    assert code(worker) == expected
    assert worker.stdout == c.envelope_line(
        worker.plan.invocation_id, "failed", None, expected
    )
    assert b"9999" not in worker.stderr and b"Traceback" not in worker.stderr


# ---------------------------------------------------------------- lifecycle


def test_soft_deadline(tmp_path):
    late = run_worker(
        tmp_path / "startup",
        {"import_sleep": 2.0, "responses": [{"calls": [finish_call()]}]},
        soft_in=1.0,
    )
    assert code(late) == "forge_deadline_exceeded" and "run" not in kinds(late)
    waiting = run_worker(
        tmp_path / "await", {"responses": [{"sleep": 30}]}, soft_in=1.5
    )
    assert code(waiting) == "forge_deadline_exceeded" and waiting.elapsed < 15
    overrun = run_worker(
        tmp_path / "sync",
        {"responses": [{"block": 2.5, "sleep": 0.05, "calls": [finish_call()]}]},
        soft_in=1.5,
    )
    assert code(overrun) == "forge_deadline_exceeded"  # Seen at the next await.
    slow_close = run_worker(
        tmp_path / "aclose",
        {"aclose": {"sleep": 30}, "responses": [{"calls": [finish_call()]}]},
        soft_in=NORMAL_SOFT,  # Must complete; only the 2 s aclose bound applies.
    )
    ok(slow_close)
    assert "aclose" in kinds(slow_close) and slow_close.elapsed < 10


def test_aclose_failure_does_not_change_code(tmp_path):
    worker = run_worker(
        tmp_path, {"aclose": {"raise": True}, "responses": [{"calls": [finish_call()]}]}
    )
    ok(worker)


# ---------------------------------------------------------------- tools


def test_tool_exceptions_fixed_messages(tmp_path):
    ws = workspace(tmp_path)
    (ws / "file.txt").write_text("x")
    (ws / "dir").mkdir()
    worker = run_worker(
        tmp_path,
        {
            "responses": [
                {
                    "calls": [
                        ["read_file", {"path": "file.txt/below"}],
                        ["read_file", {"path": "dir"}],
                        ["list_dir", {"path": "file.txt"}],
                        ["list_dir", {"path": "absent"}],
                    ]
                },
                {"calls": [finish_call()]},
            ]
        },
    )
    ok(worker)
    assert results(worker)[:4] == [
        READ_REFUSED,
        READ_REFUSED,
        "[ToolResolutionError] list_dir refused: path unavailable",
        "[ToolResolutionError] list_dir refused: path unavailable",
    ]


@pytest.mark.parametrize("contract", [c.ADAPTER_CONTRACT, c.ADAPTER_CONTRACT_V2])
def test_read_window_defaults_by_contract(tmp_path, contract):
    data = "abcdefghij" * 110
    (workspace(tmp_path) / "data.txt").write_text(data)
    cases = [
        ({}, 0, 1024),
        ({"offset": None, "length": None}, 0, 1024),
        ({"offset": None, "length": 7}, 0, 7),
        ({"offset": 3, "length": None}, 3, 1024),
    ]
    calls = [
        ["read_file", {"path": "data.txt", **options}] for options, _, _ in cases
    ]
    worker = run_worker(
        tmp_path,
        {"responses": [{"calls": calls}, {"calls": [finish_call()]}]},
        profile={**PROFILE, "budgets": {"read_bytes_per_call": 1024}},
        contract=contract,
    )
    ok(worker)
    reads = results(worker)[:-1]
    for (options, offset, length), result in zip(cases, reads, strict=True):
        if contract == c.ADAPTER_CONTRACT and None in options.values():
            assert result == (
                "[ToolError] ValueError: read_file arguments invalid: "
                "expected path and integer offset/length"
            )
        else:
            assert json.loads(result) == {
                "path": "data.txt",
                "offset": offset,
                "length": length,
                "size": len(data),
                "eof": False,
                "text": data[offset : offset + length],
            }


@pytest.mark.parametrize("contract", [c.ADAPTER_CONTRACT, c.ADAPTER_CONTRACT_V2])
def test_strict_tool_args(tmp_path, contract):
    bad = [
        ["read_file", {"path": "f", "offset": True}],
        ["read_file", {"path": "f", "offset": 1.0}],
        ["read_file", {"path": "f", "offset": "1"}],
        ["read_file", {"path": "f", "offset": -1}],
        ["read_file", {"path": "f", "length": False}],
        ["read_file", {"path": "f", "length": 1.0}],
        ["read_file", {"path": "f", "length": "1"}],
        ["read_file", {"path": "f", "length": 0}],
        ["read_file", {"path": "f", "length": 32769}],
        ["read_file", {"path": "f", "extra": 1}],
        ["read_file", {"offset": 0}],
        ["list_dir", {}],
        ["list_dir", {"path": "", "extra": 1}],
        ["list_dir", {"path": 1}],
        ["finish", {"outcome": "READY", "summary": "x"}],
    ]
    worker = run_worker(
        tmp_path,
        {"responses": [{"calls": bad}, {"calls": [finish_call()]}]},
        contract=contract,
    )
    ok(worker)
    for text in results(worker)[: len(bad)]:
        assert text.startswith("[ToolError] ValueError: ") or text.startswith(
            "[ToolResolutionError] finish refused"
        ), text


def test_path_walk_refusals(tmp_path):
    ws = workspace(tmp_path)
    (ws / "data.txt").write_text("hello")
    (ws / "link").symlink_to("data.txt")
    (ws / "dirlink").symlink_to(".")
    (ws / "sub").mkdir()
    (ws / ".git").mkdir()
    (ws / ".git" / "config").write_text("x")
    (ws / ".millrace-router-owner").write_text("x")
    (ws / "bin.dat").write_bytes(b"\xff\xfe")
    (ws / "split.txt").write_text("é")
    os.mkfifo(ws / "pipe")
    paths = [
        "link",
        "dirlink/data.txt",
        "../data.txt",
        "/etc/hostname",
        ".git/config",
        ".GIT/config",
        "sub/../data.txt",
        ".millrace-router-owner",
        "pipe",
        "bin.dat",
        "sub//x",
        "a\x00b",
        "x" * 1025,
    ]
    calls = [["read_file", {"path": path}] for path in paths]
    calls.append(["read_file", {"path": "split.txt", "length": 1024, "offset": 1}])
    calls.append(["list_dir", {"path": "dirlink"}])
    worker = run_worker(
        tmp_path,
        {
            "record_opens": True,
            "responses": [
                {"calls": calls},
                {"calls": [finish_call()]},
            ],
        },
        timeout=30,
    )  # A FIFO opened by mistake would block until this timeout.
    ok(worker)
    for text in results(worker)[: len(calls)]:
        assert text.startswith("[ToolResolutionError]"), text
    assert "pipe" not in opened(worker)  # The real FIFO is refused before open.


def opened(worker) -> list[str]:
    return [e["name"] for e in worker.events if e["kind"] == "opened"]


def test_path_walk_refuses_device_and_fifo_before_open(tmp_path):
    """Fake stat reports a char device and a FIFO; no real device is created."""
    ws = workspace(tmp_path)
    for name in ("devnode", "fifonode", "data.txt"):
        (ws / name).write_text("x")
    worker = run_worker(
        tmp_path,
        {
            "record_opens": True,
            "fake_modes": {"devnode": "S_IFCHR", "fifonode": "S_IFIFO"},
            "responses": [
                {
                    "calls": [
                        ["read_file", {"path": "devnode"}],
                        ["read_file", {"path": "fifonode"}],
                        ["read_file", {"path": "data.txt"}],
                    ]
                },
                {"calls": [finish_call()]},
            ],
        },
    )
    ok(worker)
    assert results(worker)[:2] == [READ_REFUSED, READ_REFUSED]
    assert json.loads(results(worker)[2])["text"] == "x"
    assert opened(worker) == ["data.txt"]  # Neither target was ever opened.


def test_path_walk_identity_swap(tmp_path):
    ws = workspace(tmp_path)
    (ws / "data.txt").write_text("original")
    (ws / "other.txt").write_text("swapped")
    worker = run_worker(
        tmp_path,
        {
            "swap": {
                "name": "data.txt",
                "target": str(ws / "data.txt"),
                "replacement": str(ws / "other.txt"),
            },
            "responses": [
                {"calls": [["read_file", {"path": "data.txt"}]]},
                {"calls": [finish_call()]},
            ],
        },
    )
    ok(worker)
    assert results(worker)[0].startswith("[ToolResolutionError] read_file refused")


def test_tool_bounds_and_finish_fit(tmp_path):
    ws = workspace(tmp_path)
    for index in range(20):
        (ws / f"f{index:02d}").write_text("x")
    (ws / ".git").mkdir()
    (ws / "big.txt").write_text("abcdefghij")
    try:
        os.close(os.open(os.fsencode(ws) + b"/bad\xff", os.O_CREAT | os.O_WRONLY))
        undecodable = 1
    except OSError:  # Some filesystems refuse non-UTF-8 names.
        undecodable = 0
    profile = {**PROFILE, "budgets": {"list_entries_max": 16, "max_tool_errors": 3}}
    worker = run_worker(
        tmp_path,
        {
            "responses": [
                {
                    "calls": [
                        ["list_dir", {"path": ""}],
                        ["read_file", {"path": "big.txt", "offset": 2, "length": 1024}],
                    ]
                },
                {"calls": [finish_call(summary="s" * 16385)]},
                {"calls": [finish_call(refs=["../x"])]},
                {"calls": [finish_call(refs=["r"] * 33)]},
                {
                    "calls": [["list_dir", {"path": "sub-absent"}]]
                },  # Resets the error count.
                {"calls": [finish_call(summary=QUOTES)]},
                {"calls": [finish_call(summary="s" * 16384, refs=["big.txt"])]},
            ]
        },
        profile=profile,
    )
    assert ok(worker)["summary"] == "s" * 16384
    listing = json.loads(results(worker)[0])
    assert listing["truncated"] is True and len(listing["entries"]) == 16
    names = [entry["name"] for entry in listing["entries"]]
    assert names == sorted(names, key=lambda n: n.encode()) and ".git" not in names
    assert listing["omitted_undecodable"] == undecodable
    window = json.loads(results(worker)[1])
    assert window == {
        "path": "big.txt",
        "offset": 2,
        "length": 8,
        "size": 10,
        "eof": True,
        "text": "cdefghij",
    }
    for text in results(worker)[2:5]:
        assert text.startswith("[ToolError] ValueError: finish arguments invalid")
    # Valid arguments whose double-encoded envelope exceeds 65,537 bytes.
    semantic = json.dumps(
        {"outcome": "READY", "summary": QUOTES, "refs": []},
        separators=(",", ":"),
        ensure_ascii=False,
    )
    assert len(QUOTES.encode()) <= 16384 and len(semantic.encode()) <= 65536
    assert len(c.envelope_line("1" * 32, "completed", semantic, None)) > 65537
    assert results(worker)[6] == (
        "[ToolError] ValueError: finish arguments invalid: "
        "expected a declared outcome, summary and refs"
    )


def test_tool_budgets(tmp_path):
    ws = workspace(tmp_path)
    (ws / "big.txt").write_text("y" * 65536)
    profile = {**PROFILE, "budgets": {"tool_calls_max": 1}}
    listing = {"calls": [["list_dir", {"path": ""}]]}
    worker = run_worker(
        tmp_path / "calls",
        {
            "responses": [
                listing,
                listing,
                {"calls": [finish_call()]},
            ]
        },
        profile=profile,
    )
    ok(worker)
    assert results(worker)[1] == (
        "[ToolResolutionError] list_dir refused: read budget exhausted"
    )
    shutil.copy(ws / "big.txt", workspace(tmp_path / "bytes") / "big.txt")
    profile = {
        **PROFILE,
        "budgets": {"read_bytes_per_call": 65536, "tool_output_bytes_total": 65536},
    }
    worker = run_worker(
        tmp_path / "bytes",
        {
            "responses": [
                {"calls": [["read_file", {"path": "big.txt"}]]},
                {"calls": [["list_dir", {"path": ""}]]},
                {"calls": [finish_call()]},
            ]
        },
        profile=profile,
    )
    ok(worker)
    assert all(text.endswith("read budget exhausted") for text in results(worker)[:2])
    profile = {**PROFILE, "budgets": {"max_iterations": 2}}
    worker = run_worker(
        tmp_path / "iterations",
        {
            "responses": [
                listing,
                listing,
            ]
        },
        profile=profile,
    )
    assert code(worker) == "forge_max_iterations"
