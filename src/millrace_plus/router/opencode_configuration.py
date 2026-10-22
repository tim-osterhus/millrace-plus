"""Pure pinned OpenCode profile, launch and captured-completion validation."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
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
    mapping,
    number,
    relative,
    strict_json,
    text,
)

ADAPTER_ID = "opencode_run"
ADAPTER_CONTRACT = "millrace-opencode-run/v1"
PROFILE_FORMAT = "millrace-opencode-run-profile/v1"
LAUNCH_FORMAT = "millrace-opencode-run-launch/v1"
RESULT_FORMAT = "millrace-opencode-run-result/v1"
OPENCODE_VERSION = "1.19.3"
PROMPT_NAME = "opencode-prompt.txt"
CONTROL_LIMIT = 1048576
EVENT_LIMIT = 8388608
STDERR_LIMIT = 1048576
LINE_LIMIT = 1048576
SETTINGS = {"model", "variant"}
LAUNCH_FIELDS = {
    "format",
    "profile_path",
    "profile_sha256",
    "prompt_source",
    "prompt_source_sha256",
    "prompt_path",
    "prompt_sha256",
    "expected_version",
    "native_inheritance",
    "environment_names",
    "cwd",
    "common_settings",
    "result_contract",
    "event_limit",
    "stderr_limit",
    "line_limit",
}
EVIDENCE_FIELDS = {
    "format",
    "invocation_id",
    "phase",
    "observed_version",
    "probe_pid",
    "session_id",
    "protocol_state",
    "event_count",
    "event_bytes",
    "error_count",
    "stream_sha256",
    "stderr_bytes",
    "stderr_sha256",
    "final",
    "failure_code",
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
            or not 1 <= len(value) <= 256
            or value.startswith("-")
            or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in value)
        ):
            raise RouterError("invalid_opencode_settings")
        if key == "model" and ("/" not in value or not all(value.split("/", 1))):
            raise RouterError("invalid_opencode_settings")
    return obj


def _control(files: Mapping[str, bytes], value: object) -> tuple[str, bytes]:
    path = relative(value)
    if path not in files or type(files[path]) is not bytes:
        raise RouterError("missing_control")
    return path, files[path]


def binding(
    config: Mapping[str, object], files: Mapping[str, bytes]
) -> tuple[str, bytes, dict[str, object]]:
    path, raw = _control(files, fields(config, {"profile"})["profile"])
    if len(raw) > CONTROL_LIMIT:
        raise RouterError("invalid_opencode_profile")
    obj = fields(strict_json(raw), {"format", "opencode_version"}, SETTINGS)
    if obj["format"] != PROFILE_FORMAT:
        raise RouterError("invalid_opencode_profile")
    if obj["opencode_version"] != OPENCODE_VERSION:
        raise RouterError("unsupported_opencode_version")
    settings_layer({k: v for k, v in obj.items() if k in SETTINGS})
    return path, raw, obj


def node(
    config: Mapping[str, object], files: Mapping[str, bytes]
) -> tuple[str, bytes, dict[str, object]]:
    obj = fields(config, {"prompt"}, SETTINGS)
    path, raw = _control(files, obj["prompt"])
    try:
        decoded = raw.decode("utf-8")
        if (
            len(raw) > CONTROL_LIMIT
            or "\x00" in decoded
            or not decoded.lstrip("\ufeff").strip()
        ):
            raise RouterError("invalid_opencode_prompt")
    except UnicodeError as exc:
        raise RouterError("invalid_opencode_prompt") from exc
    return path, raw, settings_layer({k: v for k, v in obj.items() if k in SETTINGS})


def _resolve(*layers: tuple[str, Mapping[str, object]]) -> dict[str, object]:
    values: dict[str, object] = {}
    provenance: dict[str, object] = {}
    for source, layer in layers:
        for key in sorted(SETTINGS & layer.keys()):
            values[key] = layer[key]
            provenance[key] = {"source": source, "policy": None}
    return {
        "requested": values.copy(),
        "effective": values,
        "provenance": provenance,
        "warnings": [],
    }


def validate_configuration(config: AdapterConfiguration) -> None:
    _contract(config.adapter_id, config.adapter_contract)
    settings_layer(config.runtime)
    settings_layer(config.workflow)
    for layer in config.planes.values():
        settings_layer(layer)
    profiles = {
        key: binding(value, config.files)[2] for key, value in config.bindings.items()
    }
    for (plane_id, _), (selected, value) in config.nodes.items():
        if selected not in profiles or plane_id not in config.planes:
            raise RouterError("invalid_adapter_settings")
        node(value, config.files)


def argv(executable: str, effective: Mapping[str, object]) -> tuple[str, ...]:
    result = [executable, "run", "--format", "json"]
    for key in sorted(SETTINGS):
        if key in effective:
            result.extend(("--" + key, cast(str, effective[key])))
    return tuple(result)


def plan(request: InvocationRequest) -> LaunchPlan:
    _contract(request.adapter_id, request.adapter_contract)
    runtime, workflow, plane = (
        settings_layer(value)
        for value in (request.runtime, request.workflow, request.plane)
    )
    profile_path, profile_raw, parsed = binding(request.binding_config, request.files)
    prompt_source, prompt_raw, overrides = node(request.node_config, request.files)
    if request.result_contract != "json-v1":
        raise RouterError("invalid_opencode_result_contract")
    settings = _resolve(
        ("runtime", runtime),
        ("profile", parsed),
        ("workflow", workflow),
        ("plane", plane),
        ("node", overrides),
    )
    details = {
        "format": LAUNCH_FORMAT,
        "profile_path": profile_path,
        "profile_sha256": digest(profile_raw),
        "prompt_source": prompt_source,
        "prompt_source_sha256": digest(prompt_raw),
        "prompt_path": str(request.invocation_root / PROMPT_NAME),
        "prompt_sha256": digest(prompt_raw),
        "expected_version": OPENCODE_VERSION,
        "native_inheritance": True,
        "environment_names": list(request.environment_names),
        "cwd": str(request.project_root),
        "common_settings": dict(request.common_settings),
        "result_contract": "json-v1",
        "event_limit": EVENT_LIMIT,
        "stderr_limit": STDERR_LIMIT,
        "line_limit": LINE_LIMIT,
    }
    return LaunchPlan(
        request.invocation_id,
        ADAPTER_ID,
        ADAPTER_CONTRACT,
        argv(request.executable, mapping(settings["effective"])),
        {},
        {PROMPT_NAME: prompt_raw},
        settings,
        details,
    )


def _hash(value: object) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise RouterError("completion_integrity")


def launch_details(plan: LaunchPlan) -> dict[str, object]:
    _contract(plan.adapter_id, plan.adapter_contract)
    obj = fields(plan.details, LAUNCH_FIELDS)
    if (
        obj["format"] != LAUNCH_FORMAT
        or obj["expected_version"] != OPENCODE_VERSION
        or obj["native_inheritance"] is not True
        or obj["result_contract"] != "json-v1"
        or (obj["event_limit"], obj["stderr_limit"], obj["line_limit"])
        != (EVENT_LIMIT, STDERR_LIMIT, LINE_LIMIT)
    ):
        raise RouterError("invalid_launch_plan")
    for key in ("profile_path", "prompt_source", "prompt_path", "cwd"):
        text(obj[key])
    for key in ("profile_sha256", "prompt_source_sha256", "prompt_sha256"):
        _hash(obj[key])
    names = obj["environment_names"]
    if not isinstance(names, (list, tuple)) or any(
        not isinstance(name, str) for name in names
    ):
        raise RouterError("invalid_launch_plan")
    mapping(obj["common_settings"])
    selected = fields(
        plan.settings, {"requested", "effective", "provenance", "warnings"}
    )
    effective = settings_layer(selected["effective"])
    if (
        selected["requested"] != effective
        or set(mapping(selected["provenance"])) != set(effective)
        or selected["warnings"] != []
    ):
        raise RouterError("invalid_launch_plan")
    for value in mapping(selected["provenance"]).values():
        source = fields(value, {"source", "policy"})
        if (
            source["source"] not in {"runtime", "profile", "workflow", "plane", "node"}
            or source["policy"] is not None
        ):
            raise RouterError("invalid_launch_plan")
    if (
        not plan.argv
        or plan.argv != argv(plan.argv[0], effective)
        or plan.environment
        or set(plan.generated_controls) != {PROMPT_NAME}
        or type(plan.generated_controls[PROMPT_NAME]) is not bytes
        or digest(plan.generated_controls[PROMPT_NAME]) != obj["prompt_sha256"]
        or obj["prompt_source_sha256"] != obj["prompt_sha256"]
    ):
        raise RouterError("invalid_launch_plan")
    return obj


def evidence(invocation_id: str) -> dict[str, object]:
    return {
        "format": RESULT_FORMAT,
        "invocation_id": invocation_id,
        "phase": "preflight",
        "observed_version": None,
        "probe_pid": None,
        "session_id": None,
        "protocol_state": "not_started",
        "event_count": 0,
        "event_bytes": 0,
        "error_count": 0,
        "stream_sha256": None,
        "stderr_bytes": 0,
        "stderr_sha256": None,
        "final": None,
        "failure_code": None,
    }


def _positive(value: object) -> bool:
    return type(value) is int and value > 0


def validate_completion(plan: LaunchPlan, completion: CompletionFacts) -> None:
    """Check captured links and generic facts only; never touch native state."""
    try:
        launch_details(plan)
        e = fields(completion.evidence, EVIDENCE_FIELDS)
        if (
            completion.invocation_id != plan.invocation_id
            or e["invocation_id"] != plan.invocation_id
            or e["format"] != RESULT_FORMAT
            or e["phase"] not in {"preflight", "invocation"}
            or e["observed_version"] not in {None, OPENCODE_VERSION}
            or e["protocol_state"]
            not in {"not_started", "incomplete", "invalid", "settled"}
        ):
            raise RouterError("completion_integrity")
        for key in ("event_count", "event_bytes", "error_count", "stderr_bytes"):
            number(e[key], integer=True, zero=True)
        for count, hashed in (
            ("event_bytes", "stream_sha256"),
            ("stderr_bytes", "stderr_sha256"),
        ):
            if (e[count] == 0) != (e[hashed] is None):
                raise RouterError("completion_integrity")
            if e[hashed] is not None:
                _hash(e[hashed])
        if e["probe_pid"] is not None and not _positive(e["probe_pid"]):
            raise RouterError("completion_integrity")
        if e["observed_version"] is not None and not _positive(e["probe_pid"]):
            raise RouterError("completion_integrity")
        if e["session_id"] is not None and len(text(e["session_id"]).encode()) > 4096:
            raise RouterError("completion_integrity")
        if cast(int, e["error_count"]) > cast(int, e["event_count"]):
            raise RouterError("completion_integrity")
        if e["phase"] == "preflight":
            if (
                completion.pid is not None
                or e["session_id"] is not None
                or e["final"] is not None
                or e["protocol_state"] != "not_started"
                or any(e[k] != 0 for k in ("event_count", "event_bytes", "error_count"))
            ):
                raise RouterError("completion_integrity")
        elif (
            not _positive(e["probe_pid"])
            or e["observed_version"] != OPENCODE_VERSION
            or e["protocol_state"] == "not_started"
        ):
            raise RouterError("completion_integrity")
        if completion.transport == "completed":
            final = fields(e["final"], {"message_id", "reason", "size", "text_sha256"})
            if (
                e["phase"] != "invocation"
                or not _positive(completion.pid)
                or type(completion.exit_code) is not int
                or completion.exit_code != 0
                or completion.writer_state != "stopped"
                or e["protocol_state"] != "settled"
                or e["session_id"] is None
                or not _positive(e["event_count"])
                or not 1 <= cast(int, e["event_bytes"]) <= EVENT_LIMIT
                or cast(int, e["event_count"]) > cast(int, e["event_bytes"])
                or cast(int, e["stderr_bytes"]) > STDERR_LIMIT
                or e["error_count"] != 0
                or e["failure_code"] is not None
                or len(text(final["message_id"]).encode()) > 4096
                or final["reason"] != "stop"
                or type(final["size"]) is not int
                or final["size"] != len(completion.stdout)
                or not 1 <= len(completion.stdout) <= EVENT_LIMIT
                or final["text_sha256"] != digest(completion.stdout)
            ):
                raise RouterError("completion_integrity")
        else:
            code = text(e["failure_code"])
            if (
                completion.stdout
                or e["final"] is not None
                or len(code) > 128
                or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789_" for ch in code)
            ):
                raise RouterError("completion_integrity")
    except (
        RouterError,
        KeyError,
        TypeError,
        ValueError,
        UnicodeError,
        RecursionError,
    ) as exc:
        raise RouterError("completion_integrity") from exc
