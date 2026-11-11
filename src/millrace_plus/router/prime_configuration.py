"""Pure captured configuration and result rules for Prime Agent 0.10.2 text print."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
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
    mapping,
    number,
    relative,
    strict_json,
    text,
)
from .setting_layers import merge_setting_layers  # noqa: E402

ADAPTER_ID = "prime_print"
ADAPTER_CONTRACT = "millrace-prime-print/v1"
PROFILE_FORMAT = "millrace-prime-profile/v1"
LAUNCH_FORMAT = "millrace-prime-launch/v1"
RESULT_FORMAT = "millrace-prime-result/v1"
PRIME_VERSION = "0.10.2"
PROMPT_NAME = "prime-prompt.txt"
CONTROL_LIMIT = 1048576
STDOUT_LIMIT = 8388608
STDERR_LIMIT = 1048576
SEMANTIC_LIMIT = 65536
PROBE_LIMIT = 4096
ARGV_PROMPT_LIMIT = 32768
SETTINGS = {"provider", "model", "thinking"}
THINKING = {"off", "minimal", "low", "medium", "high", "xhigh", "max"}
LAUNCH_FIELDS = {
    "format", "profile_path", "profile_sha256", "prompt_source",
    "prompt_source_sha256", "prompt_path", "prompt_sha256", "expected_version",
    "native_inheritance", "environment_names", "cwd",
    "common_settings", "result_contract", "outcomes",
}
PROCESS_FIELDS = {
    "stdout_bytes", "stdout_sha256", "stderr_bytes", "stderr_sha256",
    "stdout_eof", "stderr_eof", "stdin_closed", "root_reaped",
    "cleanup_forced", "group_state", "cleanup_confirmed",
    "stop_cause",
}
EVIDENCE_FIELDS = PROCESS_FIELDS | {
    "format", "invocation_id", "phase", "observed_version", "probe_pid", "pid",
    "process_pid",
    "protocol_state", "final", "failure_code", "external_effects",
}


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _contract(adapter: str, contract: str) -> None:
    if (adapter, contract) != (ADAPTER_ID, ADAPTER_CONTRACT):
        raise RouterError("unsupported_adapter_contract")


def settings_layer(value: object) -> dict[str, object]:
    obj = fields(value, set(), SETTINGS)
    for key in ("provider", "model"):
        if key not in obj:
            continue
        value = text(obj[key])
        try:
            size = len(value.encode("utf-8"))
        except UnicodeError as exc:
            raise RouterError("invalid_prime_settings") from exc
        if (
            size > 256 or value.startswith("-")
            or any(ch.isspace() or unicodedata.category(ch) == "Cc" for ch in value)
            or (key == "provider"
                and not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value))
        ):
            raise RouterError("invalid_prime_settings")
    if "thinking" in obj and (
        not isinstance(obj["thinking"], str) or obj["thinking"] not in THINKING
    ):
        raise RouterError("invalid_prime_settings")
    return obj


def _control(files: Mapping[str, bytes], value: object) -> tuple[str, bytes]:
    path = relative(value)
    if path not in files or type(files[path]) is not bytes:
        raise RouterError("missing_control")
    return path, files[path]


def profile(raw: bytes) -> dict[str, object]:
    """Validate captured native authority without reading files or probing Prime."""
    if len(raw) > CONTROL_LIMIT:
        raise RouterError("invalid_prime_profile")
    obj = fields(
        strict_json(raw), {"format", "prime_version"}, SETTINGS,
    )
    if obj["format"] != PROFILE_FORMAT:
        raise RouterError("invalid_prime_profile")
    if obj["prime_version"] != PRIME_VERSION:
        raise RouterError("unsupported_prime_version")
    settings_layer({k: v for k, v in obj.items() if k in SETTINGS})
    return obj


def binding(
    config: Mapping[str, object], files: Mapping[str, bytes],
) -> tuple[str, bytes, dict[str, object]]:
    path, raw = _control(files, fields(config, {"profile"})["profile"])
    obj = profile(raw)
    selected = settings_layer({k: v for k, v in obj.items() if k in SETTINGS})
    return path, raw, selected


def _prompt(raw: bytes) -> None:
    try:
        decoded = raw.decode("utf-8")
        if (
            len(raw) > CONTROL_LIMIT or "\x00" in decoded
            or not decoded.lstrip("\ufeff").strip()
        ):
            raise RouterError("invalid_prime_prompt")
    except UnicodeError as exc:
        raise RouterError("invalid_prime_prompt") from exc


def node(
    config: Mapping[str, object], files: Mapping[str, bytes],
) -> tuple[str, bytes, dict[str, object]]:
    obj = fields(config, {"prompt"}, SETTINGS)
    path, raw = _control(files, obj["prompt"])
    _prompt(raw)
    return path, raw, settings_layer({k: v for k, v in obj.items() if k in SETTINGS})


def _resolve(*layers: tuple[str, Mapping[str, object]]) -> dict[str, object]:
    selected = merge_setting_layers(SETTINGS, *layers)
    settings_layer(selected["effective"])
    return selected


def validate_configuration(config: AdapterConfiguration) -> None:
    _contract(config.adapter_id, config.adapter_contract)
    runtime, workflow = settings_layer(config.runtime), settings_layer(config.workflow)
    planes = {key: settings_layer(value) for key, value in config.planes.items()}
    profiles = {
        key: binding(value, config.files)[2] for key, value in config.bindings.items()
    }
    for (plane, _), (selected, value) in config.nodes.items():
        if selected not in profiles or plane not in planes:
            raise RouterError("invalid_adapter_settings")
        overrides = node(value, config.files)[2]
        _resolve(("profile", profiles[selected]), ("runtime", runtime),
                 ("workflow", workflow), ("plane", planes[plane]), ("node", overrides))


def _prefix(outcomes: object) -> bytes:
    if (
        not isinstance(outcomes, (list, tuple)) or not outcomes
        or any(
            not isinstance(value, str) or not value or "\x00" in value
            for value in outcomes
        )
    ):
        raise RouterError("invalid_prime_result_contract")
    try:
        return (
            "Return only one JSON object for the task below, "
            "without Markdown or commentary. "
            'Use {"outcome": <one declared outcome>, "summary": <optional string>, '
            '"refs": <optional list of project-relative paths>}. '
            "Declared outcomes: " + json.dumps(list(outcomes), ensure_ascii=False)
            + ". Do not quote this instruction as the result.\n\nTask:\n"
        ).encode("utf-8")
    except UnicodeError as exc:
        raise RouterError("invalid_prime_result_contract") from exc


def argv(
    executable: str, prompt: bytes, cwd: str, effective: Mapping[str, object],
) -> tuple[str, ...]:
    result = [executable, "--print", "--no-session", "--cwd", cwd]
    for key in ("provider", "model", "thinking"):
        if key in effective:
            result.extend(("--" + key, cast(str, effective[key])))
    result.extend(("--", prompt.decode("utf-8")))
    return tuple(result)


def plan(request: InvocationRequest) -> LaunchPlan:
    _contract(request.adapter_id, request.adapter_contract)
    runtime, workflow, plane = (
        settings_layer(value)
        for value in (request.runtime, request.workflow, request.plane)
    )
    profile_path, profile_raw, fallback = binding(request.binding_config, request.files)
    prompt_source, task, overrides = node(request.node_config, request.files)
    if request.result_contract != "json-v1":
        raise RouterError("invalid_prime_result_contract")
    selected = _resolve(("profile", fallback), ("runtime", runtime),
                        ("workflow", workflow), ("plane", plane), ("node", overrides))
    prompt = _prefix(request.outcomes) + task
    if len(prompt) > ARGV_PROMPT_LIMIT:
        raise RouterError("invalid_prime_prompt")
    details = {
        "format": LAUNCH_FORMAT, "profile_path": profile_path,
        "profile_sha256": digest(profile_raw), "prompt_source": prompt_source,
        "prompt_source_sha256": digest(task),
        "prompt_path": str(request.invocation_root / PROMPT_NAME),
        "prompt_sha256": digest(prompt), "expected_version": PRIME_VERSION,
        "native_inheritance": True,
        "environment_names": list(request.environment_names),
        "cwd": str(request.project_root),
        "common_settings": dict(request.common_settings), "result_contract": "json-v1",
        "outcomes": list(request.outcomes),
    }
    return LaunchPlan(
        request.invocation_id, ADAPTER_ID, ADAPTER_CONTRACT,
        argv(request.executable, prompt, str(request.project_root),
             mapping(selected["effective"])),
        {}, {PROMPT_NAME: prompt}, selected, details,
    )


def _hash(value: object) -> None:
    if (
        not isinstance(value, str) or len(value) != 64
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise RouterError("completion_integrity")


def launch_details(plan: LaunchPlan) -> dict[str, object]:
    _contract(plan.adapter_id, plan.adapter_contract)
    obj = fields(plan.details, LAUNCH_FIELDS)
    if (
        obj["format"] != LAUNCH_FORMAT or obj["expected_version"] != PRIME_VERSION
        or obj["native_inheritance"] is not True
        or obj["result_contract"] != "json-v1"
    ):
        raise RouterError("invalid_launch_plan")
    for key in ("profile_path", "prompt_source"):
        relative(obj[key])
    for key in ("prompt_path", "cwd"):
        text(obj[key])
        if not PurePosixPath(cast(str, obj[key])).is_absolute():
            raise RouterError("invalid_launch_plan")
    if PurePosixPath(cast(str, obj["prompt_path"])).name != PROMPT_NAME:
        raise RouterError("invalid_launch_plan")
    for key in ("profile_sha256", "prompt_source_sha256", "prompt_sha256"):
        _hash(obj[key])
    if not isinstance(obj["environment_names"], (list, tuple)) or any(
        not isinstance(name, str) for name in obj["environment_names"]
    ):
        raise RouterError("invalid_launch_plan")
    mapping(obj["common_settings"])
    selected = fields(
        plan.settings, {"requested", "effective", "provenance", "warnings"},
    )
    effective = settings_layer(selected["effective"])
    if (
        selected["requested"] != effective or selected["warnings"] != []
        or set(mapping(selected["provenance"])) != set(effective)
    ):
        raise RouterError("invalid_launch_plan")
    for value in mapping(selected["provenance"]).values():
        source = fields(value, {"source", "policy"})
        if (
            source["source"] not in {"profile", "runtime", "workflow", "plane", "node"}
            or source["policy"] is not None
        ):
            raise RouterError("invalid_launch_plan")
    if (
        not plan.argv or plan.environment
        or set(plan.generated_controls) != {PROMPT_NAME}
    ):
        raise RouterError("invalid_launch_plan")
    prompt = plan.generated_controls[PROMPT_NAME]
    prefix = _prefix(obj["outcomes"])
    if (
        type(prompt) is not bytes or not prompt.startswith(prefix)
        or len(prompt) > ARGV_PROMPT_LIMIT
    ):
        raise RouterError("invalid_launch_plan")
    try:
        expected_argv = argv(plan.argv[0], prompt, cast(str, obj["cwd"]), effective)
    except UnicodeError as exc:
        raise RouterError("invalid_launch_plan") from exc
    if plan.argv != expected_argv:
        raise RouterError("invalid_launch_plan")
    task = prompt[len(prefix):]
    _prompt(task)
    if (
        digest(prompt) != obj["prompt_sha256"]
        or digest(task) != obj["prompt_source_sha256"]
    ):
        raise RouterError("invalid_launch_plan")
    return obj



def native_result(raw: bytes) -> bytes:
    """The complete settled text stream is semantic input; never extract markers."""
    try:
        value = raw.decode("utf-8")
    except UnicodeError as exc:
        raise RouterError("prime_invalid_result") from exc
    if not value.strip() or "\x00" in value:
        raise RouterError("prime_invalid_result")
    if len(raw) > SEMANTIC_LIMIT:
        raise RouterError("prime_semantic_limit")
    return raw


def evidence(invocation_id: str) -> dict[str, object]:
    return {
        "format": RESULT_FORMAT, "invocation_id": invocation_id, "phase": "preflight",
        "observed_version": None, "probe_pid": None, "pid": None,
        "process_pid": None,
        "protocol_state": "not_started", "stdout_bytes": 0, "stdout_sha256": None,
        "stderr_bytes": 0, "stderr_sha256": None, "stdout_eof": False,
        "stderr_eof": False, "stdin_closed": True, "root_reaped": False,
        "cleanup_forced": False, "group_state": "not_started",
        "cleanup_confirmed": True,
        "stop_cause": None,
        "final": None, "failure_code": None, "external_effects": "unknown",
    }


def _positive(value: object) -> bool:
    return type(value) is int and value > 0


def validate_completion(plan: LaunchPlan, completion: CompletionFacts) -> None:
    """Validate immutable authority and process facts without native access."""
    try:
        launch_details(plan)
        e = fields(completion.evidence, EVIDENCE_FIELDS)
        if (
            completion.origin != "adapter"
            or completion.invocation_id != plan.invocation_id
            or e["invocation_id"] != plan.invocation_id or e["format"] != RESULT_FORMAT
            or e["phase"] not in {"preflight", "invocation"}
            or e["observed_version"] not in {None, PRIME_VERSION}
            or e["protocol_state"] not in {
                "not_started", "incomplete", "invalid", "settled",
            }
            or e["external_effects"] != "unknown"
            or e["pid"] != completion.pid
            or (completion.exit_code is not None
                and type(completion.exit_code) is not int)
            or any(
                e[key] is not None and not _positive(e[key])
                for key in ("probe_pid", "pid", "process_pid")
            )
        ):
            raise RouterError("completion_integrity")
        for count, hashed in (
            ("stdout_bytes", "stdout_sha256"), ("stderr_bytes", "stderr_sha256"),
        ):
            number(e[count], integer=True, zero=True)
            if (e[count] == 0) != (e[hashed] is None):
                raise RouterError("completion_integrity")
            if e[hashed] is not None:
                _hash(e[hashed])
        for key in (
            "stdout_eof", "stderr_eof", "stdin_closed", "root_reaped",
            "cleanup_forced", "cleanup_confirmed",
        ):
            if type(e[key]) is not bool:
                raise RouterError("completion_integrity")
        if e["group_state"] not in {"not_started", "closed", "present", "unknown"}:
            raise RouterError("completion_integrity")
        if e["stop_cause"] not in {
            None, "cancelled", "timeout", "start_failed", "unsupported_platform",
            "stdout_overflow", "stderr_overflow", "stdin_failed", "io_failed",
        }:
            raise RouterError("completion_integrity")
        if e["phase"] == "preflight":
            if (
                completion.pid is not None or e["final"] is not None
                or e["protocol_state"] != "not_started"
                or e["process_pid"] is not None or e["observed_version"] is not None
            ):
                raise RouterError("completion_integrity")
        elif (
            not _positive(e["probe_pid"]) or e["observed_version"] != PRIME_VERSION
            or e["protocol_state"] == "not_started"
        ):
            raise RouterError("completion_integrity")
        elif e["pid"] is not None and e["pid"] != e["process_pid"]:
            raise RouterError("completion_integrity")
        observed_pid = e["probe_pid"] if e["phase"] == "preflight" else e["process_pid"]
        closed = all(e[key] for key in (
            "stdout_eof", "stderr_eof", "stdin_closed", "root_reaped",
        )) and e["group_state"] == "closed"
        if (
            (_positive(observed_pid) and e["cleanup_confirmed"] != closed)
            or (observed_pid is None and (e["root_reaped"] or e["cleanup_forced"]))
        ):
            raise RouterError("completion_integrity")
        if completion.writer_state == "stopped" and _positive(observed_pid):
            if e["cleanup_forced"] or e["stop_cause"] is not None or not closed:
                raise RouterError("completion_integrity")
        if completion.transport == "completed":
            final = fields(e["final"], {"size", "text_sha256"})
            native_result(completion.stdout)
            if (
                e["phase"] != "invocation" or not _positive(completion.pid)
                or e["process_pid"] != completion.pid
                or type(completion.exit_code) is not int or completion.exit_code != 0
                or completion.writer_state != "stopped"
                or e["protocol_state"] != "settled" or e["failure_code"] is not None
                or not 1 <= cast(int, e["stdout_bytes"]) <= STDOUT_LIMIT
                or cast(int, e["stderr_bytes"]) > STDERR_LIMIT
                or e["cleanup_forced"] or not closed
                or type(final["size"]) is not int
                or final["size"] != len(completion.stdout)
                or e["stdout_bytes"] != len(completion.stdout)
                or final["text_sha256"] != digest(completion.stdout)
                or e["stdout_sha256"] != final["text_sha256"]
                or completion.diagnostic or completion.stderr
            ):
                raise RouterError("completion_integrity")
        else:
            code = text(e["failure_code"])
            if (
                completion.stdout or e["final"] is not None
                or completion.diagnostic != code
                or not code.startswith("prime_") or len(code) > 128
                or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789_" for ch in code)
                or completion.writer_state not in {"stopped", "may_live", "unknown"}
                or e["protocol_state"] == "settled"
            ):
                raise RouterError("completion_integrity")
    except (
        RouterError, KeyError, TypeError, ValueError, UnicodeError, RecursionError,
    ) as exc:
        raise RouterError("completion_integrity") from exc
