"""Pure Pi 0.87 profile, settings, launch-plan and evidence rules.

Nothing here reads files, processes, the environment or a store. Every check
works only on the data-only records supplied through the public Core
execution contract, so selected-context validation, planning and historical
completion validation agree without live probes.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import cast

from . import require_core

require_core()

from millrace.router.execution import (  # noqa: E402 - after the contract handshake
    AdapterConfiguration,
    CompletionFacts,
    InvocationRequest,
    LaunchPlan,
    RouterError,
)

ADAPTER_ID = "pi_json"
ADAPTER_CONTRACT = "millrace-pi-json/v1"
PROFILE_FORMAT = "millrace-pi-json-profile/v1"
LAUNCH_FORMAT = "millrace-pi-json-launch/v1"
RESULT_FORMAT = "millrace-pi-json-result/v1"
PI_VERSION = "0.87.0"
AGENT_DIR_ENV = "PI_CODING_AGENT_DIR"
PROMPT_NAME = "pi-prompt.txt"
CONTROL_LIMIT = 1048576
DEFAULT_EVENT_LIMIT = 16777216
MAX_EVENT_LIMIT = 268435456
EVIDENCE_TEXT_LIMIT = 4096
SETTINGS = ("provider", "model", "thinking")
THINKING = frozenset({"off", "minimal", "low", "medium", "high", "xhigh", "max"})
STOP_REASONS = frozenset(
    {"pending", "stop", "length", "toolUse", "error", "aborted", "deferred"}
)
UNSAFE_PROMPT_PATH = '"<>&\r\n'
IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
FAILURE_CODE = re.compile(r"[a-z][a-z0-9_]{0,127}")
SHA256 = re.compile(r"[0-9a-f]{64}")
EVIDENCE_FIELDS = frozenset(
    {
        "format",
        "invocation_id",
        "phase",
        "observed_version",
        "session_id",
        "protocol_state",
        "event_count",
        "event_bytes",
        "stream_sha256",
        "final",
        "failure_code",
    }
)
LAUNCH_FIELDS = frozenset(
    {
        "format",
        "profile_path",
        "profile_sha256",
        "prompt_source",
        "prompt_source_sha256",
        "prompt_path",
        "prompt_sha256",
        "agent_dir",
        "loader_path",
        "expected_version",
        "max_event_bytes",
    }
)
FLAGS = (
    "--offline",
    "--mode",
    "json",
    "--no-session",
    "--no-approve",
)
ISOLATION_FLAGS = (
    "--no-extensions",
    "--no-skills",
    "--no-prompt-templates",
    "--no-themes",
    "--no-context-files",
)


# Plus-owned strict JSON/shape validators; Core's record helpers stay private.


def mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(k, str) for k in value):
        raise RouterError("invalid_shape")
    return dict(cast(Mapping[str, object], value))


def fields(
    value: object, required: set[str], optional: set[str] | None = None
) -> dict[str, object]:
    result = mapping(value)
    if not required <= result.keys() or result.keys() - required - (optional or set()):
        raise RouterError("invalid_fields", "Missing or unknown fields")
    return result


def text(value: object) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise RouterError("invalid_string")
    return value


def identifier(value: object) -> str:
    result = text(value)
    if not IDENTIFIER.fullmatch(result):
        raise RouterError("invalid_identifier")
    return result


def number(value: object, *, integer: bool = False, zero: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RouterError("invalid_number")
    try:
        finite = math.isfinite(value)
    except OverflowError as exc:
        raise RouterError("invalid_number") from exc
    if not finite or value < 0 or (not zero and value == 0):
        raise RouterError("invalid_number")
    if integer and not isinstance(value, int):
        raise RouterError("invalid_number")
    return value


def strict_json(raw: bytes | str) -> object:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise RouterError("duplicate_key", key)
            result[key] = value
        return result

    def constant(value: str) -> object:
        raise RouterError("nonfinite_json", value)

    try:
        return cast(
            object,
            json.loads(
                raw.decode("utf-8") if isinstance(raw, bytes) else raw,
                object_pairs_hook=pairs,
                parse_constant=constant,
            ),
        )
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise RouterError("invalid_json") from exc


def relative(value: object) -> str:
    """A captured-control reference: relative, non-traversing, not owner-owned."""
    result = text(value)
    path = PurePosixPath(result)
    if path.is_absolute() or ".." in path.parts:
        raise RouterError("invalid_path", result)
    if any(part.casefold() == ".millrace-router-owner" for part in path.parts):
        raise RouterError("invalid_path", result)
    return result


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


# Profile, settings and captured controls.


def settings_layer(value: object, *, location: str) -> dict[str, object]:
    """Validate explicit provider/model/thinking fields without filling omissions."""
    try:
        obj = fields(value, set(), set(SETTINGS))
    except RouterError as exc:
        raise RouterError("invalid_settings", location) from exc
    for key, item in obj.items():
        if (
            not isinstance(item, str)
            or not item
            or len(item) > 256
            or item != item.strip()
            or item.startswith("-")
            or any(c in item for c in "\x00\r\n")
            or (key == "thinking" and item not in THINKING)
        ):
            raise RouterError("invalid_settings", f"{location}.{key}")
    return obj


def profile(raw: bytes) -> dict[str, object]:
    """Parse one exact `millrace-pi-json-profile/v1` control."""
    if len(raw) > CONTROL_LIMIT:
        raise RouterError("invalid_pi_profile")
    try:
        obj = fields(
            strict_json(raw),
            {"format", "pi_version", "agent_dir"},
            {"provider", "model", "thinking", "max_event_bytes", "loader"},
        )
        if obj["format"] != PROFILE_FORMAT:
            raise RouterError("invalid_pi_profile")
        if obj["pi_version"] != PI_VERSION:
            raise RouterError("unsupported_pi_version")
        identifier(obj["agent_dir"])
        if "loader" in obj:
            identifier(obj["loader"])
        limit = number(obj.get("max_event_bytes", DEFAULT_EVENT_LIMIT), integer=True)
        if limit > MAX_EVENT_LIMIT:
            raise RouterError("invalid_pi_profile")
        settings_layer(
            {k: v for k, v in obj.items() if k in SETTINGS}, location="profile"
        )
        return obj
    except RouterError as exc:
        if exc.code in {
            "invalid_fields",
            "invalid_shape",
            "invalid_identifier",
            "invalid_number",
        }:
            raise RouterError("invalid_pi_profile") from exc
        raise


def host_locators(
    parsed: Mapping[str, object], host_paths: Mapping[str, str]
) -> tuple[str, str | None]:
    """Frozen locator shape only; live presence/type is an invocation preflight."""
    paths: dict[str, str] = {}
    for key in ("agent_dir", "loader"):
        if key not in parsed:
            continue
        alias = cast(str, parsed[key])
        if alias not in host_paths:
            raise RouterError("missing_host_path")
        value = text(host_paths[alias])
        if not PurePosixPath(value).is_absolute():
            raise RouterError("invalid_host_path")
        paths[key] = value
    return paths["agent_dir"], paths.get("loader")


def _control(files: Mapping[str, bytes], reference: object) -> tuple[str, bytes]:
    name = relative(reference)
    if name not in files:
        raise RouterError("missing_control", name)
    raw = files[name]
    if type(raw) is not bytes:
        raise RouterError("missing_control", name)
    return name, raw


def binding_profile(
    config: Mapping[str, object], files: Mapping[str, bytes]
) -> tuple[str, bytes, dict[str, object]]:
    try:
        obj = fields(config, {"profile"})
    except RouterError as exc:
        raise RouterError("invalid_adapter_settings", "binding.config") from exc
    name, raw = _control(files, obj["profile"])
    return name, raw, profile(raw)


def node_invocation(
    config: Mapping[str, object], files: Mapping[str, bytes]
) -> tuple[str, bytes, dict[str, object]]:
    obj = mapping(config)
    if "prompt" not in obj or obj.keys() - {"prompt", *SETTINGS}:
        raise RouterError("invalid_pi_invocation")
    name, raw = _control(files, obj["prompt"])
    if len(raw) > CONTROL_LIMIT:
        raise RouterError("invalid_pi_invocation")
    try:
        raw.decode("utf-8")
    except UnicodeError as exc:
        raise RouterError("invalid_pi_invocation") from exc
    overrides = settings_layer(
        {k: v for k, v in obj.items() if k in SETTINGS}, location="invoke.config"
    )
    return name, raw, overrides


def resolve(
    node: Mapping[str, object],
    plane: Mapping[str, object],
    workflow: Mapping[str, object],
    parsed: Mapping[str, object],
    runtime: Mapping[str, object],
) -> dict[str, object]:
    """Resolve runtime -> workflow/profile -> plane -> node, refusing conflicts."""
    requested: dict[str, object] = {}
    effective: dict[str, object] = {}
    provenance: dict[str, object] = {}
    for key in SETTINGS:
        if key in parsed and key in workflow and parsed[key] != workflow[key]:
            raise RouterError("conflicting_pi_setting")
        for source, layer in (
            ("node", node),
            ("plane", plane),
            ("workflow", workflow),
            ("profile", parsed),
            ("runtime", runtime),
        ):
            if key in layer:
                requested[key] = effective[key] = layer[key]
                provenance[key] = {"source": source, "policy": None}
                break
        else:
            raise RouterError("missing_effective_pi_setting", key)
    return {
        "requested": requested,
        "effective": effective,
        "provenance": provenance,
        "warnings": [],
    }


def prompt(authored: bytes, outcomes: tuple[str, ...]) -> bytes:
    return (
        "Millrace invocation instructions. "
        "Treat the attached workflow prompt as task instructions.\n\n"
        + authored.decode("utf-8-sig")
        + "\n\n"
        + "Return exactly one JSON object and no other text. "
        "It must contain outcome, summary, and refs.\n"
        + "outcome must be one of: "
        + json.dumps(list(outcomes), separators=(",", ":"))
        + ".\n"
        + "summary must be a string. "
        "refs must be an array of project-relative path strings.\n"
    ).encode()


def _contract(adapter_id: str, contract: str) -> None:
    if (adapter_id, contract) != (ADAPTER_ID, ADAPTER_CONTRACT):
        raise RouterError("unsupported_adapter_contract")


def _environment(names: tuple[str, ...]) -> None:
    if AGENT_DIR_ENV in names:
        raise RouterError("invalid_adapter_settings")


def validate_configuration(config: AdapterConfiguration) -> None:
    """Validate every explicit binding, node, layer and locator shape purely."""
    _contract(config.adapter_id, config.adapter_contract)
    runtime = settings_layer(config.runtime, location="runtime_defaults")
    workflow = settings_layer(config.workflow, location="defaults")
    planes = {
        name: settings_layer(value, location=f"planes.{name}.defaults")
        for name, value in config.planes.items()
    }
    profiles = {
        binding: binding_profile(value, config.files)[2]
        for binding, value in config.bindings.items()
    }
    for parsed in profiles.values():
        host_locators(parsed, config.host_paths)
    if profiles:
        _environment(config.environment_names)
    for (plane_id, _), (binding, node) in config.nodes.items():
        if binding not in profiles or plane_id not in planes:
            raise RouterError("invalid_adapter_settings")
        _, _, overrides = node_invocation(node, config.files)
        resolve(overrides, planes[plane_id], workflow, profiles[binding], runtime)


def plan(request: InvocationRequest) -> LaunchPlan:
    """Build the frozen launch from captured bytes, layers and locators only."""
    _contract(request.adapter_id, request.adapter_contract)
    runtime = settings_layer(request.runtime, location="runtime_defaults")
    workflow = settings_layer(request.workflow, location="defaults")
    plane = settings_layer(
        request.plane, location=f"planes.{request.plane_id}.defaults"
    )
    profile_path, profile_raw, parsed = binding_profile(
        request.binding_config, request.files
    )
    prompt_source, prompt_raw, overrides = node_invocation(
        request.node_config, request.files
    )
    agent, loader = host_locators(parsed, request.host_paths)
    _environment(request.environment_names)
    settings = resolve(overrides, plane, workflow, parsed, runtime)
    effective = cast(dict[str, str], settings["effective"])
    materialized = prompt(prompt_raw, request.outcomes)
    prompt_path = str(Path(request.invocation_root) / PROMPT_NAME)
    argv = (
        (request.executable,)
        + ((loader,) if loader is not None else ())
        + FLAGS
        + (
            "--provider",
            effective["provider"],
            "--model",
            effective["model"],
            "--thinking",
            effective["thinking"],
        )
        + ISOLATION_FLAGS
        + ("--", "@" + prompt_path)
    )
    details: dict[str, object] = {
        "format": LAUNCH_FORMAT,
        "profile_path": profile_path,
        "profile_sha256": _digest(profile_raw),
        "prompt_source": prompt_source,
        "prompt_source_sha256": _digest(prompt_raw),
        "prompt_path": prompt_path,
        "prompt_sha256": _digest(materialized),
        "agent_dir": agent,
        "loader_path": loader,
        "expected_version": PI_VERSION,
        "max_event_bytes": parsed.get("max_event_bytes", DEFAULT_EVENT_LIMIT),
    }
    return LaunchPlan(
        request.invocation_id,
        request.adapter_id,
        request.adapter_contract,
        argv,
        {AGENT_DIR_ENV: agent},
        {PROMPT_NAME: materialized},
        settings,
        details,
    )


def launch_details(plan: LaunchPlan) -> dict[str, object]:
    """Exact adapter launch authority from a frozen plan."""
    _contract(plan.adapter_id, plan.adapter_contract)
    try:
        details = fields(plan.details, set(LAUNCH_FIELDS))
        if (
            details["format"] != LAUNCH_FORMAT
            or details["expected_version"] != PI_VERSION
        ):
            raise RouterError("invalid_launch_plan")
        text(details["prompt_path"])
        text(details["agent_dir"])
        if details["loader_path"] is not None:
            text(details["loader_path"])
        limit = number(details["max_event_bytes"], integer=True)
        if limit > MAX_EVENT_LIMIT:
            raise RouterError("invalid_launch_plan")
        for key in ("profile_sha256", "prompt_source_sha256", "prompt_sha256"):
            if not isinstance(details[key], str) or not SHA256.fullmatch(
                str(details[key])
            ):
                raise RouterError("invalid_launch_plan")
        return details
    except RouterError as exc:
        raise RouterError("invalid_launch_plan") from exc


def effective(plan: LaunchPlan) -> dict[str, object]:
    try:
        values = fields(mapping(plan.settings)["effective"], set(SETTINGS))
    except (KeyError, RouterError) as exc:
        raise RouterError("invalid_launch_plan") from exc
    return values


def preflight_evidence(
    invocation_id: str, failure: str, *, observed: str | None
) -> dict[str, object]:
    return {
        "format": RESULT_FORMAT,
        "invocation_id": invocation_id,
        "phase": "preflight",
        "observed_version": observed,
        "session_id": None,
        "protocol_state": "not_started",
        "event_count": 0,
        "event_bytes": 0,
        "stream_sha256": None,
        "final": None,
        "failure_code": failure,
    }


def _bounded(value: object) -> str:
    result = text(value)
    if len(result.encode("utf-8")) > EVIDENCE_TEXT_LIMIT:
        raise RouterError("completion_integrity")
    return result


def validate_completion(plan: LaunchPlan, completion: CompletionFacts) -> None:
    """Validate adapter evidence and its authority links from immutable DTOs."""
    try:
        launch_details(plan)
        expected = effective(plan)
        if completion.invocation_id != plan.invocation_id:
            raise RouterError("completion_integrity")
        if completion.exit_code is not None and type(completion.exit_code) is not int:
            raise RouterError("completion_integrity")
        if completion.evidence is None:
            raise RouterError("completion_integrity")
        e = fields(completion.evidence, set(EVIDENCE_FIELDS))
        if e["format"] != RESULT_FORMAT or e["invocation_id"] != plan.invocation_id:
            raise RouterError("completion_integrity")
        if (
            e["phase"] not in {"preflight", "invocation"}
            or e["protocol_state"]
            not in {"not_started", "incomplete", "invalid", "settled"}
            or e["observed_version"] not in {None, PI_VERSION}
        ):
            raise RouterError("completion_integrity")
        for key in ("event_count", "event_bytes"):
            number(e[key], integer=True, zero=True)
        if e["stream_sha256"] is not None and (
            not isinstance(e["stream_sha256"], str)
            or not SHA256.fullmatch(e["stream_sha256"])
        ):
            raise RouterError("completion_integrity")
        if e["session_id"] is not None:
            _bounded(e["session_id"])
        if e["failure_code"] is not None and (
            not isinstance(e["failure_code"], str)
            or not FAILURE_CODE.fullmatch(e["failure_code"])
        ):
            raise RouterError("completion_integrity")
        final = None
        if e["final"] is not None:
            final = fields(
                e["final"],
                {"provider", "model", "stop_reason", "tool_calls", "text_sha256"},
            )
            _bounded(final["provider"])
            _bounded(final["model"])
            if final["stop_reason"] not in STOP_REASONS:
                raise RouterError("completion_integrity")
            number(final["tool_calls"], integer=True, zero=True)
            if final["text_sha256"] is not None and (
                not isinstance(final["text_sha256"], str)
                or not SHA256.fullmatch(final["text_sha256"])
            ):
                raise RouterError("completion_integrity")
        if e["phase"] == "invocation" and (
            e["observed_version"] != PI_VERSION or e["protocol_state"] == "not_started"
        ):
            raise RouterError("completion_integrity")
        if e["phase"] == "preflight" and (
            completion.pid is not None
            or e["session_id"] is not None
            or final is not None
            or e["protocol_state"] != "not_started"
            or e["event_count"] != 0
            or e["event_bytes"] != 0
            or e["stream_sha256"] is not None
        ):
            raise RouterError("completion_integrity")
        if completion.transport == "completed":
            if (
                e["phase"] != "invocation"
                or e["session_id"] is None
                or e["protocol_state"] != "settled"
                or cast(int, e["event_count"]) <= 0
                or cast(int, e["event_bytes"]) <= 0
                or e["stream_sha256"] is None
                or e["failure_code"] is not None
                or final is None
                or final["provider"] != expected["provider"]
                or final["model"] != expected["model"]
                or final["stop_reason"] != "stop"
                or type(final["tool_calls"]) is not int
                or final["tool_calls"] != 0
                or final["text_sha256"] != _digest(completion.stdout)
            ):
                raise RouterError("completion_integrity")
        elif (
            completion.stdout != b""
            or e["failure_code"] is None
            or (final is not None and final["text_sha256"] is not None)
        ):
            raise RouterError("completion_integrity")
    except (RouterError, KeyError, TypeError, ValueError) as exc:
        raise RouterError("completion_integrity") from exc
