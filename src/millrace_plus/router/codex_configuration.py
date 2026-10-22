"""Pure Codex configuration, deterministic plans and completion evidence."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import cast

from . import require_core

require_core()

from millrace.router.execution import (  # noqa: E402
    AdapterConfiguration,
    CompletionFacts,
    InvocationRequest,
    LaunchPlan,
    RouterError,
)

from .configuration import (  # noqa: E402
    fields,
    identifier,
    mapping,
    number,
    relative,
    strict_json,
    text,
)

ADAPTER_ID = "codex_exec"
ADAPTER_CONTRACT = "millrace-codex-exec/v1"
PROFILE_FORMAT = "millrace-codex-exec-profile/v1"
LAUNCH_FORMAT = "millrace-codex-exec-launch/v1"
RESULT_FORMAT = "millrace-codex-exec-result/v1"
CODEX_VERSION = "0.163.0"
CONTROL_LIMIT = 1048576
EVENT_LIMIT = 16777216
LINE_LIMIT = 1048576
FINAL_LIMIT = 65536
PROMPT_NAME = "codex-prompt.txt"
SCHEMA_NAME = "codex-result-schema.json"
FINAL_NAME = "codex-final.json"
SETTINGS = {"model", "reasoning_effort"}
EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}
SANDBOXES = {"read-only", "workspace-write", "danger-full-access"}
EVIDENCE_FIELDS = {
    "format",
    "invocation_id",
    "phase",
    "observed_version",
    "probe_pid",
    "thread_id",
    "protocol_state",
    "event_count",
    "event_bytes",
    "error_count",
    "stream_sha256",
    "final",
    "stderr_truncated",
    "failure_code",
}
LAUNCH_FIELDS = {
    "format",
    "profile_path",
    "profile_sha256",
    "prompt_source",
    "prompt_source_sha256",
    "prompt_path",
    "prompt_sha256",
    "schema_path",
    "schema_sha256",
    "final_path",
    "codex_home",
    "expected_version",
    "auth_mode",
    "sandbox",
    "event_limit",
    "line_limit",
    "final_limit",
}


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _contract(adapter: str, contract: str) -> None:
    if (adapter, contract) != (ADAPTER_ID, ADAPTER_CONTRACT):
        raise RouterError("unsupported_adapter_contract")


def settings_layer(value: object) -> dict[str, object]:
    obj = fields(value, set(), SETTINGS)
    for key, value in obj.items():
        if (
            not isinstance(value, str)
            or not value
            or len(value) > 256
            or value != value.strip()
            or value.startswith("-")
            or any(c in value for c in "\x00\r\n")
            or (key == "reasoning_effort" and value not in EFFORTS)
        ):
            raise RouterError("invalid_codex_settings")
    return obj


def profile(raw: bytes) -> dict[str, object]:
    if len(raw) > CONTROL_LIMIT:
        raise RouterError("invalid_codex_profile")
    obj = fields(
        strict_json(raw),
        {"format", "codex_version", "codex_home", "auth_mode", "sandbox"},
        SETTINGS,
    )
    if obj["format"] != PROFILE_FORMAT:
        raise RouterError("invalid_codex_profile")
    if obj["codex_version"] != CODEX_VERSION:
        raise RouterError("unsupported_codex_version")
    identifier(obj["codex_home"])
    if obj["auth_mode"] != "chatgpt" or (
        not isinstance(obj["sandbox"], str) or obj["sandbox"] not in SANDBOXES
    ):
        raise RouterError("invalid_codex_profile")
    settings_layer({k: v for k, v in obj.items() if k in SETTINGS})
    return obj


def _control(files: Mapping[str, bytes], name: object) -> tuple[str, bytes]:
    key = relative(name)
    if key not in files or type(files[key]) is not bytes:
        raise RouterError("missing_control")
    return key, files[key]


def binding(
    config: Mapping[str, object], files: Mapping[str, bytes]
) -> tuple[str, bytes, dict[str, object]]:
    obj = fields(config, {"profile"})
    path, raw = _control(files, obj["profile"])
    return path, raw, profile(raw)


def node(
    config: Mapping[str, object], files: Mapping[str, bytes]
) -> tuple[str, bytes, dict[str, object]]:
    obj = fields(config, {"prompt"}, SETTINGS)
    path, raw = _control(files, obj["prompt"])
    try:
        if len(raw) > CONTROL_LIMIT or "\x00" in raw.decode("utf-8-sig"):
            raise RouterError("invalid_codex_prompt")
    except UnicodeError as exc:
        raise RouterError("invalid_codex_prompt") from exc
    return path, raw, settings_layer({k: v for k, v in obj.items() if k in SETTINGS})


def locator(parsed: Mapping[str, object], paths: Mapping[str, str]) -> str:
    alias = cast(str, parsed["codex_home"])
    if alias not in paths:
        raise RouterError("missing_host_path")
    value = text(paths[alias])
    if not PurePosixPath(value).is_absolute():
        raise RouterError("invalid_host_path")
    return value


def resolve(
    node_values: Mapping[str, object],
    plane: Mapping[str, object],
    workflow: Mapping[str, object],
    parsed: Mapping[str, object],
    runtime: Mapping[str, object],
) -> dict[str, object]:
    values: dict[str, object] = {}
    provenance: dict[str, object] = {}
    for key in sorted(SETTINGS):
        if key in parsed and key in workflow and parsed[key] != workflow[key]:
            raise RouterError("conflicting_codex_setting")
        for source, layer in (
            ("node", node_values),
            ("plane", plane),
            ("workflow", workflow),
            ("profile", parsed),
            ("runtime", runtime),
        ):
            if key in layer:
                values[key] = layer[key]
                provenance[key] = {"source": source, "policy": None}
                break
        else:
            raise RouterError("missing_effective_codex_setting", key)
    return {
        "requested": values.copy(),
        "effective": values,
        "provenance": provenance,
        "warnings": [],
    }


def _environment(names: tuple[str, ...]) -> None:
    if "CODEX_HOME" in names:
        raise RouterError("invalid_adapter_settings")


def validate_configuration(config: AdapterConfiguration) -> None:
    _contract(config.adapter_id, config.adapter_contract)
    _environment(config.environment_names)
    runtime, workflow = settings_layer(config.runtime), settings_layer(config.workflow)
    planes = {name: settings_layer(value) for name, value in config.planes.items()}
    profiles = {
        key: binding(value, config.files)[2] for key, value in config.bindings.items()
    }
    for parsed in profiles.values():
        locator(parsed, config.host_paths)
    for (plane_id, _), (selected, config_node) in config.nodes.items():
        if selected not in profiles or plane_id not in planes:
            raise RouterError("invalid_adapter_settings")
        resolve(
            node(config_node, config.files)[2],
            planes[plane_id],
            workflow,
            profiles[selected],
            runtime,
        )


def plan(request: InvocationRequest) -> LaunchPlan:
    _contract(request.adapter_id, request.adapter_contract)
    _environment(request.environment_names)
    runtime, workflow = (
        settings_layer(request.runtime),
        settings_layer(request.workflow),
    )
    plane = settings_layer(request.plane)
    profile_path, profile_raw, parsed = binding(request.binding_config, request.files)
    prompt_source, prompt_raw, overrides = node(request.node_config, request.files)
    native_home = locator(parsed, request.host_paths)
    settings = resolve(overrides, plane, workflow, parsed, runtime)
    effective = cast(dict[str, str], settings["effective"])
    prompt = (
        "Millrace invocation instructions. "
        "Treat the authored prompt as task instructions.\n\n"
        + prompt_raw.decode("utf-8-sig")
        + "\n\nReturn exactly one JSON object with outcome, summary, and refs. "
        + "outcome must be one of: "
        + json.dumps(list(request.outcomes), separators=(",", ":"))
        + ". summary is a string; refs is an array of project-relative paths.\n"
    ).encode()
    schema = json.dumps(
        {
            "type": "object",
            "properties": {
                "outcome": {"type": "string", "enum": list(request.outcomes)},
                "summary": {"type": "string"},
                "refs": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["outcome", "summary", "refs"],
            "additionalProperties": False,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    prompt_path, schema_path, final_path = (
        str(request.invocation_root / name)
        for name in (PROMPT_NAME, SCHEMA_NAME, FINAL_NAME)
    )
    argv = [request.executable, "exec"]
    for key, value in (
        ("model_provider", "openai"),
        ("forced_login_method", "chatgpt"),
        ("cli_auth_credentials_store", "file"),
        ("approval_policy", "never"),
        ("model_reasoning_effort", effective["reasoning_effort"]),
    ):
        argv.extend(("-c", key + "=" + json.dumps(value)))
    argv.extend(
        (
            "--json",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--skip-git-repo-check",
            "--color",
            "never",
            "--sandbox",
            cast(str, parsed["sandbox"]),
            "--model",
            effective["model"],
            "--output-schema",
            schema_path,
            "--output-last-message",
            final_path,
            "-",
        )
    )
    details = {
        "format": LAUNCH_FORMAT,
        "profile_path": profile_path,
        "profile_sha256": digest(profile_raw),
        "prompt_source": prompt_source,
        "prompt_source_sha256": digest(prompt_raw),
        "prompt_path": prompt_path,
        "prompt_sha256": digest(prompt),
        "schema_path": schema_path,
        "schema_sha256": digest(schema),
        "final_path": final_path,
        "codex_home": native_home,
        "expected_version": CODEX_VERSION,
        "auth_mode": "chatgpt",
        "sandbox": parsed["sandbox"],
        "event_limit": EVENT_LIMIT,
        "line_limit": LINE_LIMIT,
        "final_limit": FINAL_LIMIT,
    }
    return LaunchPlan(
        request.invocation_id,
        ADAPTER_ID,
        ADAPTER_CONTRACT,
        tuple(argv),
        {"CODEX_HOME": native_home},
        {PROMPT_NAME: prompt, SCHEMA_NAME: schema},
        settings,
        details,
    )


def launch_details(plan: LaunchPlan) -> dict[str, object]:
    _contract(plan.adapter_id, plan.adapter_contract)
    obj = fields(plan.details, LAUNCH_FIELDS)
    if (
        obj["format"] != LAUNCH_FORMAT
        or obj["expected_version"] != CODEX_VERSION
        or obj["auth_mode"] != "chatgpt"
        or (not isinstance(obj["sandbox"], str) or obj["sandbox"] not in SANDBOXES)
        or (obj["event_limit"], obj["line_limit"], obj["final_limit"])
        != (EVENT_LIMIT, LINE_LIMIT, FINAL_LIMIT)
    ):
        raise RouterError("invalid_launch_plan")
    for key in (
        "profile_path",
        "prompt_source",
        "prompt_path",
        "schema_path",
        "final_path",
        "codex_home",
    ):
        text(obj[key])
    for key in (
        "profile_sha256",
        "prompt_source_sha256",
        "prompt_sha256",
        "schema_sha256",
    ):
        _hash(obj[key])
    settings_layer(fields(mapping(plan.settings)["effective"], SETTINGS))
    return obj


def _hash(value: object) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise RouterError("completion_integrity")


def evidence(invocation: str) -> dict[str, object]:
    return {
        "format": RESULT_FORMAT,
        "invocation_id": invocation,
        "phase": "preflight",
        "observed_version": None,
        "probe_pid": None,
        "thread_id": None,
        "protocol_state": "not_started",
        "event_count": 0,
        "event_bytes": 0,
        "error_count": 0,
        "stream_sha256": None,
        "final": None,
        "stderr_truncated": False,
        "failure_code": None,
    }


def _positive(value: object) -> bool:
    return type(value) is int and value > 0


def validate_completion(plan: LaunchPlan, completion: CompletionFacts) -> None:
    try:
        launch_details(plan)
        e = fields(completion.evidence, EVIDENCE_FIELDS)
        if (
            completion.invocation_id != plan.invocation_id
            or e["invocation_id"] != plan.invocation_id
            or e["format"] != RESULT_FORMAT
            or e["phase"] not in {"preflight", "invocation"}
            or e["observed_version"] not in {None, CODEX_VERSION}
            or e["protocol_state"]
            not in {"not_started", "incomplete", "invalid", "settled"}
            or type(e["stderr_truncated"]) is not bool
        ):
            raise RouterError("completion_integrity")
        for key in ("event_count", "event_bytes", "error_count"):
            number(e[key], integer=True, zero=True)
        if cast(int, e["error_count"]) > cast(int, e["event_count"]):
            raise RouterError("completion_integrity")
        if (
            (e["observed_version"] is not None and not _positive(e["probe_pid"]))
            or ((e["event_bytes"] == 0) != (e["stream_sha256"] is None))
            or (e["protocol_state"] == "settled" and e["thread_id"] is None)
        ):
            raise RouterError("completion_integrity")
        if e["probe_pid"] is not None and not _positive(e["probe_pid"]):
            raise RouterError("completion_integrity")
        if e["stream_sha256"] is not None:
            _hash(e["stream_sha256"])
        if e["thread_id"] is not None and len(text(e["thread_id"]).encode()) > 4096:
            raise RouterError("completion_integrity")
        if e["failure_code"] is not None:
            code = text(e["failure_code"])
            if len(code) > 128 or any(
                c not in "abcdefghijklmnopqrstuvwxyz0123456789_" for c in code
            ):
                raise RouterError("completion_integrity")
        if e["phase"] == "preflight":
            if (
                completion.pid is not None
                or e["thread_id"] is not None
                or e["final"] is not None
                or e["protocol_state"] != "not_started"
                or any(e[k] != 0 for k in ("event_count", "event_bytes", "error_count"))
                or e["stream_sha256"] is not None
            ):
                raise RouterError("completion_integrity")
        elif (
            not _positive(e["probe_pid"])
            or e["observed_version"] != CODEX_VERSION
            or e["protocol_state"] == "not_started"
        ):
            raise RouterError("completion_integrity")
        if completion.transport == "completed":
            final = fields(e["final"], {"size", "text_sha256"})
            if (
                e["phase"] != "invocation"
                or not _positive(completion.pid)
                or completion.exit_code != 0
                or type(completion.exit_code) is not int
                or e["protocol_state"] != "settled"
                or e["thread_id"] is None
                or not _positive(e["event_count"])
                or not _positive(e["event_bytes"])
                or cast(int, e["event_bytes"]) > EVENT_LIMIT
                or cast(int, e["event_count"]) > cast(int, e["event_bytes"])
                or e["stream_sha256"] is None
                or e["failure_code"] is not None
                or type(final["size"]) is not int
                or final["size"] != len(completion.stdout)
                or not 1 <= len(completion.stdout) <= FINAL_LIMIT
                or final["text_sha256"] != digest(completion.stdout)
            ):
                raise RouterError("completion_integrity")
        elif completion.stdout or e["failure_code"] is None or e["final"] is not None:
            raise RouterError("completion_integrity")
    except (RouterError, KeyError, TypeError, ValueError, UnicodeError) as exc:
        raise RouterError("completion_integrity") from exc
