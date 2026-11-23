"""Offline tests for the v3 captured controls and the Responses send seam."""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import replace
from importlib.metadata import version
from tempfile import TemporaryFile
from types import SimpleNamespace

import pytest
from forge_support import SEMANTIC, configuration, facts, probe_line, request
from millrace.router.execution import RouterError

from millrace_plus.router import forge_configuration as c
from millrace_plus.router.resolver import resolve_adapter

PROFILE = {
    "format": c.PROFILE_FORMAT_V3,
    "forge_version": c.FORGE_VERSION,
    "backend": "openai_responses",
    "auth": {"kind": "env", "name": "OPENAI_API_KEY"},
    "model": "fixture-model",
    "reasoning_effort": "low",
}
PROMPT = [
    {"role": "system", "content": "system"},
    {"role": "user", "content": "prompt"},
]
TOOLS = [
    SimpleNamespace(name=name, description=name, get_json_schema=dict)
    for name in ("list_dir", "read_file", "finish")
]


def responses_request(root, *, profile=None, **changes):
    return request(
        root,
        profile=PROFILE if profile is None else profile,
        adapter_contract=c.ADAPTER_CONTRACT_V3,
        environment_names=("OPENAI_API_KEY",),
        **changes,
    )


def selected():
    return resolve_adapter(c.ADAPTER_ID, c.ADAPTER_CONTRACT_V3)


def test_v3_plan_and_older_control_bytes(tmp_path):
    old = request(tmp_path)
    v1 = c.plan(old)
    v2 = resolve_adapter(c.ADAPTER_ID, c.ADAPTER_CONTRACT_V2).plan(
        replace(old, adapter_contract=c.ADAPTER_CONTRACT_V2)
    )
    for name in (c.PROBE_NAME, c.REQUEST_NAME):
        assert v1.generated_controls[name] == v2.generated_controls[name]
    assert c.digest(v1.generated_controls[c.WORKER_NAME]) == c.WORKER_SHA256
    assert c.digest(v2.generated_controls[c.WORKER_NAME]) == c.WORKER_SHA256_V2

    req = responses_request(tmp_path, plane={"reasoning_effort": "high"})
    selected().validate_configuration(configuration(req))
    plan = selected().plan(req)
    assert c.digest(plan.generated_controls[c.WORKER_NAME]) == c.WORKER_SHA256_V3
    assert plan.generated_controls[c.PROBE_NAME] == v1.generated_controls[c.PROBE_NAME]
    assert plan.settings["provenance"]["reasoning_effort"]["source"] == "plane"
    assert "base_url_env" not in c.launch_details(plan, contract=c.ADAPTER_CONTRACT_V3)
    wire = json.loads(plan.generated_controls[c.REQUEST_NAME])
    assert wire["format"] == c.REQUEST_FORMAT_V3
    assert wire["backend"] == {
        "kind": "openai_responses",
        "auth": {"kind": "env", "name": "OPENAI_API_KEY"},
        "model": "fixture-model",
        "reasoning_effort": "high",
    }


@pytest.mark.parametrize(
    ("update", "code"),
    [
        ({"base_url": {"kind": "env", "name": "FORGE_URL"}}, "invalid_forge_profile"),
        ({"auth": {"kind": "none"}}, "invalid_forge_backend_reference"),
        (
            {"auth": {"kind": "env", "name": "UNDECLARED"}},
            "invalid_forge_backend_reference",
        ),
        ({"reasoning_effort": "invented"}, "invalid_forge_settings"),
    ],
)
def test_v3_rejects_invalid_profile(tmp_path, update, code):
    req = responses_request(tmp_path, profile={**PROFILE, **update})
    for operation, arg in (
        (selected().plan, req),
        (selected().validate_configuration, configuration(req)),
    ):
        with pytest.raises(RouterError) as failure:
            operation(arg)
        assert failure.value.code == code


def test_v3_requires_explicit_effort(tmp_path):
    profile = dict(PROFILE)
    profile.pop("reasoning_effort")
    with pytest.raises(RouterError) as failure:
        selected().plan(responses_request(tmp_path, profile=profile))
    assert failure.value.code == "forge_reasoning_effort_required"


def test_v3_rejects_inherited_slash_model_even_with_valid_node_model(tmp_path):
    req = responses_request(
        tmp_path,
        runtime={"model": "org/model"},
        node_config={"prompt": "task.txt", "model": "fixture-model"},
    )
    adapter = selected()
    for operation, arg in (
        (adapter.plan, req),
        (adapter.validate_configuration, configuration(req)),
    ):
        with pytest.raises(RouterError) as failure:
            operation(arg)
        assert failure.value.code == "invalid_forge_settings"

    valid = replace(req, runtime={})
    adapter.validate_configuration(configuration(valid))
    assert adapter.plan(valid).settings["provenance"]["model"]["source"] == "node"


@pytest.mark.parametrize(
    "updates",
    [
        ({"sent_effort": "high"}, {}),
        ({"tool": "finish"}, {"tool": "read_file"}),
        ({"http_status": 401}, {}),
        ({}, {"response_model": "different-model"}),
    ],
    ids=("effort", "finish-order", "status", "model"),
)
def test_v3_adapter_receipt_and_process_environment(tmp_path, monkeypatch, updates):
    from millrace_plus.router import forge

    adapter = selected()
    req = responses_request(tmp_path)
    receipt = {
        "sent_effort": "low",
        "http_status": 200,
        "response_model": "fixture-model",
        "echoed_effort": None,
        "tool": "finish",
    }
    receipts = [{**receipt, "tool": "read_file"}, receipt]

    def line(rows):
        return c.envelope_line(
            req.invocation_id, "completed", SEMANTIC, None, provider_requests=rows
        )

    wire = line(receipts)
    plan = adapter.plan(req)
    environments = []

    def run(argv, cwd, env, deadline, poll, started, *, stdin, **kwargs):
        environments.append(env)
        pid = 10 if stdin is None else 11
        stdout = probe_line() if stdin is None else wire
        started(pid)
        return SimpleNamespace(
            pid=pid,
            exit_code=0,
            stop_cause=None,
            stdout=stdout,
            stdout_bytes=len(stdout),
            stderr_bytes=0,
            stdout_sha256=c.digest(stdout),
            stderr_sha256=c.digest(b""),
            stdout_truncated=False,
            stderr_truncated=False,
            stdout_overflow=False,
            stderr_overflow=False,
            stdin_closed=True,
            stdout_eof=True,
            stderr_eof=True,
            root_reaped=True,
            cleanup_forced=False,
            group_state="closed",
            cleanup_confirmed=True,
        )

    monkeypatch.setattr(forge, "require_owned_process", lambda: None)
    monkeypatch.setattr(forge, "_run", run)
    pids = []
    result = adapter.invoke(
        plan,
        cwd=tmp_path,
        env={"OPENAI_API_KEY": "sk-fake", "HTTPS_PROXY": "127.0.0.1"},
        deadline=time.time() + 30,
        poll=lambda: False,
        on_started=pids.append,
    )
    assert environments == [{}, {"MILLRACE_FORGE_RESPONSES_KEY": "sk-fake"}]
    assert result.adapter_evidence["format"] == c.RESULT_FORMAT_V3
    completion = facts(plan, result, pids[0])
    adapter.validate_completion(plan, completion)
    envelope = completion.evidence["envelope"]
    assert envelope["provider_requests"] == receipts

    bad_receipts = [
        base | update for base, update in zip(receipts, updates, strict=True)
    ]
    bad_wire = line(bad_receipts)
    parsed, semantic = c.parse_envelope(
        bad_wire, plan.invocation_id, 0, responses=plan.details
    )
    assert parsed["parse"] == "malformed" and semantic is None

    # Match both recorded byte facts to the malformed wire before resealing.
    # A hash mismatch alone would leave the receipt validation untested.
    wire_facts = {"bytes": len(bad_wire), "sha256": c.digest(bad_wire)}
    modified = c.seal(
        {
            **completion.evidence,
            "envelope": {**envelope, **wire_facts, "provider_requests": bad_receipts},
            "main": {
                **completion.evidence["main"],
                "stdout_bytes": wire_facts["bytes"],
                "stdout_sha256": wire_facts["sha256"],
            },
        }
    )
    with pytest.raises(RouterError) as failure:
        adapter.validate_completion(plan, replace(completion, evidence=modified))
    assert failure.value.code == "completion_integrity"


@pytest.fixture
def worker(tmp_path):
    raw = selected().plan(responses_request(tmp_path)).generated_controls[c.WORKER_NAME]
    module = {"__name__": "forge_worker_fixture"}
    exec(compile(raw, c.WORKER_NAME, "exec"), module)
    return SimpleNamespace(**module)


@pytest.mark.parametrize("extra_bytes", [0, 1], ids=("at-cap", "over-cap"))
def test_v3_result_envelope_cap_includes_provider_receipts(
    worker, tmp_path, extra_bytes
):
    plan = selected().plan(responses_request(tmp_path))
    invocation_id = plan.invocation_id
    receipts = [
        {
            "sent_effort": "low",
            "http_status": 200,
            "response_model": "fixture-model",
            "echoed_effort": None,
            "tool": "finish",
        }
    ]
    body = {
        "outcome": "READY",
        "summary": "\n" * 16_384,
        "refs": ["a" * 500] * 31 + [""],
    }
    semantic = json.dumps(body, separators=(",", ":"))
    initial = c.envelope_line(
        invocation_id, "completed", semantic, None, provider_requests=receipts
    )
    padding = c.STDOUT_LIMIT - len(initial) + extra_bytes
    assert 1 <= padding <= 512
    body["refs"][-1] = "b" * padding
    semantic = json.dumps(body, separators=(",", ":"))
    without_receipts = c.envelope_line(
        invocation_id, "completed", semantic, None, provider_requests=[]
    )
    assert len(without_receipts) <= c.STDOUT_LIMIT

    with TemporaryFile() as output:
        code = worker._emit(
            output.fileno(), invocation_id, None, semantic, requests=receipts
        )
        output.seek(0)
        wire = output.read()

    parsed, result = c.parse_envelope(wire, invocation_id, code, responses=plan.details)
    assert parsed["parse"] == "ok"
    assert parsed["provider_requests"] == receipts
    if extra_bytes:
        assert code == 3
        assert parsed["status"] == "failed"
        assert parsed["worker_failure_code"] == "forge_result_too_large"
        assert result is None
    else:
        assert code == 0
        assert len(wire) == c.STDOUT_LIMIT
        assert result == semantic


def function_call(name, args, *, call_id="call_provider_0"):
    return {
        "type": "function_call",
        "call_id": call_id,
        "name": name,
        "arguments": json.dumps(args),
        "status": "completed",
    }


def responses_payload(output, **fields):
    return {
        "object": "response",
        "status": "completed",
        "model": "fixture-model-2026-11-17",
        "reasoning": {"effort": "low"},
        "output": output,
    } | fields


def forge_turn(call, index):
    return [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": f"call_sdk_{index}",
                    "function": {"name": call.tool, "arguments": json.dumps(call.args)},
                }
            ],
        },
        {
            "role": "tool",
            "name": call.tool,
            "tool_call_id": f"call_sdk_{index}",
            "content": f"result{index}",
        },
    ]


@pytest.fixture
def mock_client(worker, monkeypatch):
    httpx = pytest.importorskip("httpx")
    async_client = httpx.AsyncClient

    def create(
        handler, forge=SimpleNamespace(ToolCall=SimpleNamespace), *, budgets=None
    ):
        def check_wire(wire):
            assert str(wire.url) == worker.RESPONSES_URL
            assert wire.headers["Authorization"] == "Bearer sk-fake"
            return handler(wire)

        def in_memory(**kwargs):
            assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
            return async_client(transport=httpx.MockTransport(check_wire), **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", in_memory)
        return worker._responses_client(
            forge,
            {"model": "fixture-model", "reasoning_effort": "low"},
            budgets or {"max_iterations": 4, "http_timeout_seconds": 2},
            "sk-fake",
        )

    return httpx, create


def test_v3_replays_original_calls_and_reasoning_across_read_and_list(
    mock_client,
):
    httpx, create = mock_client
    requests = []
    outputs = []
    names = ["list_dir", "read_file", "read_file", "finish"]
    args = [
        {"path": ""},
        {"path": "sample.txt"},
        {"path": "sample.txt", "offset": 5},
        {},
    ]
    reasoning = {
        "type": "reasoning",
        "id": "rs_fixture",
        "encrypted_content": "opaque",
        "summary": [],
    }

    def response(wire):
        index = len(requests)
        body = json.loads(wire.content)
        requests.append(body)
        output = [
            function_call(names[index], args[index], call_id=f"call_provider_{index}")
        ]
        if index == 0:
            output.insert(0, reasoning)
        outputs.append(output)
        return httpx.Response(200, json=responses_payload(output))

    client = create(response)
    messages = list(PROMPT)

    async def run():
        for index, name in enumerate(names):
            (call,) = await client.send(messages, TOOLS)
            assert (call.tool, call.args) == (name, args[index])
            messages.extend(forge_turn(call, index))
        await client.aclose()

    asyncio.run(run())
    expected_input = list(PROMPT)
    for index, body in enumerate(requests):
        assert body["input"] == expected_input
        assert body["tool_choice"] == "auto" and body["parallel_tool_calls"] is False
        assert body["store"] is False and body["include"] == [
            "reasoning.encrypted_content"
        ]
        assert body["reasoning"] == {"effort": "low"}
        assert {tool["name"] for tool in body["tools"]} == set(names)
        assert all(tool["strict"] is False for tool in body["tools"])
        expected_input.extend(outputs[index])
        expected_input.append(
            {
                "type": "function_call_output",
                "call_id": f"call_provider_{index}",
                "output": f"result{index}",
            }
        )
    assert [item["tool"] for item in client.requests] == names


@pytest.mark.parametrize(
    ("scenario", "code", "http_status"),
    [
        ("auth", "forge_backend_http_error", 401),
        ("redirect", "forge_backend_http_error", 307),
        ("text-only", "forge_backend_protocol", 200),
        ("multiple", "forge_backend_protocol", 200),
        ("failed-status", "forge_backend_protocol", 200),
        ("incomplete-details", "forge_backend_protocol", 200),
        ("timeout", "forge_backend_timeout", None),
        ("transport", "forge_backend_transport", None),
    ],
)
def test_v3_response_failures(worker, mock_client, scenario, code, http_status):
    httpx, create = mock_client
    payload = responses_payload([function_call("read_file", {"path": "sample.txt"})])
    if scenario == "text-only":
        payload["output"] = [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "fixture"}],
            }
        ]
    elif scenario == "multiple":
        payload["output"].append(function_call("finish", {}, call_id="call_provider_1"))
    elif scenario == "failed-status":
        payload["status"] = "failed"
    elif scenario == "incomplete-details":
        payload["incomplete_details"] = {"reason": "max_output_tokens"}

    def response(wire):
        if scenario == "timeout":
            raise httpx.ReadTimeout("fixture timeout", request=wire)
        if scenario == "transport":
            raise httpx.ConnectError("fixture transport failure", request=wire)
        if scenario == "redirect":
            # The injected transport rejects any second target without network access.
            return httpx.Response(307, headers={"Location": "/unexpected"})
        return httpx.Response(http_status, json=payload)

    client = create(response)

    async def run():
        try:
            with pytest.raises(worker._Failure) as failure:
                await client.send(PROMPT, TOOLS)
            assert failure.value.code == code
        finally:
            await client.aclose()

    asyncio.run(run())
    assert len(client.requests) == 1
    assert client.requests[0]["http_status"] == http_status
    assert client.requests[0]["tool"] is None


@pytest.mark.parametrize("extra_bytes", [0, 1], ids=("at-cap", "over-cap"))
def test_v3_response_size_cap(worker, mock_client, extra_bytes):
    httpx, create = mock_client
    payload = json.dumps(responses_payload([function_call("finish", {})])).encode()
    body = payload.ljust(128 * 1024 + extra_bytes, b" ")
    client = create(lambda wire: httpx.Response(200, content=body))

    async def run():
        try:
            if extra_bytes:
                with pytest.raises(worker._Failure) as failure:
                    await client.send(PROMPT, TOOLS)
                assert failure.value.code == "forge_backend_protocol"
            else:
                (call,) = await client.send(PROMPT, TOOLS)
                assert call.tool == "finish"
        finally:
            await client.aclose()

    asyncio.run(run())


def test_v3_rejects_duplicate_provider_call_id(worker, mock_client):
    httpx, create = mock_client
    outputs = iter(
        [
            function_call("read_file", {"path": "sample.txt"}),
            function_call("finish", {}),
        ]
    )
    client = create(
        lambda wire: httpx.Response(200, json=responses_payload([next(outputs)]))
    )

    async def run():
        try:
            (first,) = await client.send(PROMPT, TOOLS)
            with pytest.raises(worker._Failure) as failure:
                await client.send(PROMPT + forge_turn(first, 0), TOOLS)
            assert failure.value.code == "forge_backend_protocol"
        finally:
            await client.aclose()

    asyncio.run(run())
    assert [receipt["tool"] for receipt in client.requests] == ["read_file", None]


@pytest.mark.parametrize("key", [None, "sk-", "foreign-key", "sk-fake\n"])
def test_v3_rejects_bad_key(worker, monkeypatch, key):
    name = "MILLRACE_FORGE_RESPONSES_KEY"
    if key is None:
        monkeypatch.delenv(name, raising=False)
    else:
        monkeypatch.setenv(name, key)
    with pytest.raises(worker._Failure) as failure:
        worker._responses_key()
    assert failure.value.code == "forge_backend_config_invalid"
    assert name not in os.environ


def test_v3_real_forge_runner_preserves_worker_failure(
    worker, mock_client, tmp_path, monkeypatch
):
    forge = pytest.importorskip("forge")
    if version("forge-guardrails") != c.FORGE_VERSION:
        pytest.skip("this case requires the pinned Forge SDK")
    worker._check_runtime()
    httpx, create = mock_client
    client = create(
        lambda wire: httpx.Response(401, content=b"private fixture body"), forge
    )
    plan = selected().plan(responses_request(tmp_path))
    native = json.loads(plan.generated_controls[c.REQUEST_NAME])
    monkeypatch.chdir(tmp_path)
    root = worker._open_workspace(str(tmp_path))
    try:
        tools = worker._Tools(native, root, forge.errors, forge.MessageType)
        runner = forge.WorkflowRunner(
            client,
            forge.ContextManager(forge.NoCompact(), budget_tokens=None),
            stream=False,
            on_message=tools.observe,
            rescue_enabled=False,
            reasoning_replay="none",
        )
        now = time.time()
        with TemporaryFile() as output:
            code = asyncio.run(
                worker._session(
                    output.fileno(),
                    native,
                    {"process_deadline_unix": now + 10, "soft_deadline_unix": now + 7},
                    client,
                    runner,
                    worker._workflow(forge, tools),
                    tools,
                    forge.errors,
                )
            )
            output.seek(0)
            wire = output.read()
    finally:
        os.close(root)

    assert code == 3
    assert b"private fixture body" not in wire and b"sk-fake" not in wire
    parsed, semantic = c.parse_envelope(
        wire, plan.invocation_id, code, responses=plan.details
    )
    assert (
        parsed["parse"] == "ok"
        and parsed["worker_failure_code"] == "forge_backend_http_error"
    )
    assert semantic is None and tools.log == []
    assert parsed["provider_requests"] == [
        {
            "http_status": 401,
            "sent_effort": "low",
            "echoed_effort": None,
            "response_model": None,
            "tool": None,
        }
    ]
