"""Pure Forge runner rules for ``forge_runner`` / ``millrace-forge-runner/v1``.

Profile, settings, planning, probe grammar, envelope parsing, disposition and
completion validation. Nothing here reads the environment, runs a process or
contacts a backend. Planning reads only the two packaged worker scripts through
``importlib.resources`` and checks them against pinned digests.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from importlib import resources
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

from .configuration import fields, mapping, relative, strict_json, text  # noqa: E402
from .setting_layers import merge_setting_layers  # noqa: E402

ADAPTER_ID = "forge_runner"
ADAPTER_CONTRACT = "millrace-forge-runner/v1"
PROFILE_FORMAT = "millrace-forge-runner-profile/v1"
LAUNCH_FORMAT = "millrace-forge-runner-launch/v1"
RESULT_FORMAT = "millrace-forge-runner-result/v1"
REQUEST_FORMAT = "millrace-forge-runner-request/v1"
START_FORMAT = "millrace-forge-runner-start/v1"
ENVELOPE_FORMAT = "millrace-forge-runner-worker/v1"
PROBE_FORMAT = "millrace-forge-runner-probe/v1"
FORGE_VERSION = "0.10.1"
WORKER_NAME = "forge-worker.py"
PROBE_NAME = "forge-probe.py"
REQUEST_NAME = "forge-request.json"
# Pinned identity of the packaged scripts. Any change to their bytes needs a
# new contract version and resolver pair (W-RV-03).
WORKER_SHA256 = "1205bdd9248be2a15f6f621083e618d9a3536682b47bd96714b8beeecb850afb"
PROBE_SHA256 = "95ddac9c6ae44fde351c84bfce8a63c9d934959247e8b1ad51f8bdee41d8a629"
WORKER_SCRIPT_MAX = 65536
PROBE_SCRIPT_MAX = 16384
REQUEST_MAX = 1032192
GENERATED_LIMIT = 1114112
PROFILE_MAX = 1048576
PROMPT_MAX = 1048576
SEMANTIC_LIMIT = EVIDENCE_LIMIT = 65536
STDOUT_LIMIT = 65537
STDERR_LIMIT = 1048576
PROBE_LIMIT = 4096
START_LIMIT = 4096
OUTCOMES_MAX = 64
PYTHON_ARGS = ("-I", "-B", "-X", "utf8")
PROBE_RESERVE = 25
MAIN_RESERVE = 20
CLEANUP_RESERVE = 5
SOFT_RESERVE = 3
SETTINGS = {"model"}
SOURCES = ("profile", "runtime", "workflow", "plane", "node")
BUDGETS = {
    "max_iterations": (16, 1, 32),
    "max_retries_per_step": (2, 0, 3),
    "max_tool_errors": (2, 0, 3),
    "http_timeout_seconds": (120, 1, 600),
    "tool_calls_max": (64, 1, 128),
    "read_bytes_per_call": (32768, 1024, 65536),
    "tool_output_bytes_total": (1048576, 65536, 4194304),
    "list_entries_max": (256, 16, 1024),
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
REFUSALS = frozenset({"forge_owned_process_unavailable", "forge_deadline_too_short"})
PARENT_CODES = REFUSALS | {
    "forge_cancelled",
    "forge_probe_failed",
    "forge_probe_mismatch",
    "forge_timeout",
    "forge_stdout_limit",
    "forge_stderr_limit",
    "forge_spawn_failed",
    "forge_unsupported_platform",
    "forge_io_failed",
    "forge_cleanup_unconfirmed",
    "forge_nonzero_exit",
    "forge_envelope_missing",
    "forge_envelope_malformed",
    "forge_envelope_contradiction",
}
ENVELOPE_KEYS = ("format", "invocation_id", "status", "semantic", "failure_code")
PROBE_KEYS = ("format", "python", "implementation", "forge_guardrails", "files_match")
PROCESS_FIELDS = (
    "pid",
    "exit_code",
    "stop_cause",
    "stdout_bytes",
    "stderr_bytes",
    "stdout_sha256",
    "stderr_sha256",
    "stdout_truncated",
    "stderr_truncated",
    "stdout_overflow",
    "stderr_overflow",
    "stdin_closed",
    "stdout_eof",
    "stderr_eof",
    "root_reaped",
    "cleanup_forced",
    "group_state",
    "cleanup_confirmed",
)
STOP_CAUSES = {
    None,
    "cancelled",
    "timeout",
    "start_failed",
    "unsupported_platform",
    "stdout_overflow",
    "stderr_overflow",
    "stdin_failed",
    "io_failed",
}
EVIDENCE_FIELDS = {
    "format",
    "invocation_id",
    "launch_sha256",
    "pid",
    "refusal",
    "probe",
    "probe_report",
    "main",
    "start",
    "envelope",
    "semantic",
    "failure_code",
    "receipt_sha256",
}
DETAIL_FIELDS = {
    "format",
    "adapter",
    "contract",
    "invocation_id",
    "cwd",
    "profile_path",
    "profile_sha256",
    "prompt_source",
    "prompt_source_sha256",
    "worker_path",
    "worker_sha256",
    "probe_path",
    "probe_sha256",
    "request_path",
    "request_sha256",
    "forge_version",
    "environment_names",
    "base_url_env",
    "auth",
    "model",
    "budgets",
    "result_contract",
    "outcomes",
    "common_settings",
}
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
HEX64 = re.compile(r"[0-9a-f]{64}")
PART = r"(?:0|[1-9][0-9]{0,2})"
PYTHON_GRAMMAR = re.compile(rf"{PART}\.{PART}\.{PART}")
IMPLEMENTATION_GRAMMAR = re.compile(r"[a-z0-9_]{1,32}")
VERSION_GRAMMAR = re.compile(r"[A-Za-z0-9.+!_-]{1,64}")


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def encoded(value: object) -> bytes:
    """Canonical sorted JSON for digests and seals."""
    try:
        return json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise RouterError("invalid_launch_plan") from exc


def _utf8(value: str, code: str) -> bytes:
    try:
        return value.encode("utf-8")
    except UnicodeError as exc:
        raise RouterError(code) from exc


def _contract(adapter: str, contract: str) -> None:
    if (adapter, contract) != (ADAPTER_ID, ADAPTER_CONTRACT):
        raise RouterError("unsupported_adapter_contract")


def _control(files: Mapping[str, bytes], value: object) -> tuple[str, bytes]:
    path = relative(value)
    raw = files.get(path)
    if type(raw) is not bytes:
        raise RouterError("missing_control")
    return path, raw


def packaged_scripts() -> tuple[bytes, bytes]:
    """Read the worker and probe package data. Never imports either script."""
    base = resources.files(__package__)
    return (
        base.joinpath("forge_worker.py").read_bytes(),
        base.joinpath("forge_probe.py").read_bytes(),
    )


# ---------------------------------------------------------------- profile


def env_name(value: object, environment_names: tuple[str, ...] | list[str]) -> str:
    try:
        name = text(value)
    except RouterError as exc:
        raise RouterError("invalid_forge_backend_reference") from exc
    if (
        not ENV_NAME.fullmatch(name)
        or name in {"PATH", "MILLRACE_SEAM_INPUT"}
        or name.startswith(("MILLRACE_", "PYTHON"))
        or name not in environment_names
    ):
        raise RouterError("invalid_forge_backend_reference")
    return name


def model_value(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or value.startswith("-")
        or any(ch in value for ch in "\x00\r\n")
    ):
        raise RouterError("invalid_forge_settings")
    if len(_utf8(value, "invalid_forge_settings")) > 256:
        raise RouterError("invalid_forge_settings")
    return value


def settings_layer(value: object) -> dict[str, object]:
    """One explicit Forge namespace layer: only ``model`` is supported."""
    obj = mapping(value)
    if obj.keys() - SETTINGS:
        raise RouterError("unsupported_setting")
    if "model" in obj:
        model_value(obj["model"])
    return obj


def budgets(value: object) -> dict[str, int]:
    try:
        obj = mapping(value)
    except RouterError as exc:  # null, list, number or string budgets
        raise RouterError("invalid_forge_budget") from exc
    if obj.keys() - BUDGETS.keys():
        raise RouterError("invalid_forge_budget")
    result: dict[str, int] = {}
    for key, (default, low, high) in BUDGETS.items():
        item = obj.get(key, default)
        if type(item) is not int or not low <= item <= high:
            raise RouterError("invalid_forge_budget")
        result[key] = item
    return result


def profile(raw: bytes, environment_names: tuple[str, ...]) -> dict[str, object]:
    """Strict captured profile. References only; never endpoint values."""
    if len(raw) > PROFILE_MAX:
        raise RouterError("invalid_forge_profile")
    try:
        obj = fields(
            strict_json(raw),
            {
                "format",
                "forge_version",
                "backend",
                "base_url",
                "auth",
            },
            {"model", "budgets"},
        )
    except RouterError as exc:
        raise RouterError("invalid_forge_profile") from exc
    if obj["format"] != PROFILE_FORMAT or obj["backend"] != "openai_compat":
        raise RouterError("invalid_forge_profile")
    if obj["forge_version"] != FORGE_VERSION:
        raise RouterError("unsupported_forge_version")
    try:
        base = fields(obj["base_url"], {"kind", "name"})
        auth = mapping(obj["auth"])
        if base["kind"] != "env":
            raise RouterError("invalid_forge_backend_reference")
        if auth.get("kind") == "env":
            fields(auth, {"kind", "name"})
        elif auth != {"kind": "none"}:
            raise RouterError("invalid_forge_backend_reference")
    except RouterError as exc:
        raise RouterError("invalid_forge_backend_reference") from exc
    url_name = env_name(base["name"], environment_names)
    if auth["kind"] == "env" and env_name(auth["name"], environment_names) == url_name:
        raise RouterError("invalid_forge_backend_reference")
    fallback = {"model": model_value(obj["model"])} if "model" in obj else {}
    return {
        "base_url_env": url_name,
        "auth": dict(auth),
        "fallback": fallback,
        "budgets": budgets(obj.get("budgets", {})),
    }


def binding(
    config: object, files: Mapping[str, bytes], environment_names: tuple[str, ...]
) -> tuple[str, bytes, dict[str, object]]:
    path, raw = _control(files, fields(config, {"profile"})["profile"])
    return path, raw, profile(raw, environment_names)


def node(
    config: object, files: Mapping[str, bytes]
) -> tuple[str, bytes, dict[str, object]]:
    obj = mapping(config)
    if "prompt" not in obj:
        raise RouterError("missing_control")
    if obj.keys() - {"prompt"} - SETTINGS:
        raise RouterError("unsupported_setting")
    path, raw = _control(files, obj["prompt"])
    prompt_text(raw)
    return path, raw, settings_layer({k: v for k, v in obj.items() if k != "prompt"})


def prompt_text(raw: bytes) -> str:
    try:
        value = raw.decode("utf-8")
    except UnicodeError as exc:
        raise RouterError("invalid_forge_prompt") from exc
    if len(raw) > PROMPT_MAX or "\x00" in value or not value.strip():
        raise RouterError("invalid_forge_prompt")
    return value


def resolve(*layers: tuple[str, Mapping[str, object]]) -> dict[str, object]:
    selected = merge_setting_layers(SETTINGS, *layers)
    if "model" not in selected["effective"]:
        raise RouterError("forge_model_required")
    model_value(mapping(selected["effective"])["model"])
    return selected


def validate_configuration(config: AdapterConfiguration) -> None:
    _contract(config.adapter_id, config.adapter_contract)
    runtime, workflow = settings_layer(config.runtime), settings_layer(config.workflow)
    planes = {key: settings_layer(value) for key, value in config.planes.items()}
    profiles = {
        key: binding(value, config.files, config.environment_names)[2]
        for key, value in config.bindings.items()
    }
    for (plane, _), (selected, value) in config.nodes.items():
        if selected not in profiles or plane not in planes:
            raise RouterError("invalid_adapter_settings")
        resolve(
            ("profile", mapping(profiles[selected]["fallback"])),
            ("runtime", runtime),
            ("workflow", workflow),
            ("plane", planes[plane]),
            ("node", node(value, config.files)[2]),
        )


# ---------------------------------------------------------------- planning


def outcomes(value: object, code: str) -> list[str]:
    if (
        not isinstance(value, (list, tuple))
        or not 1 <= len(value) <= OUTCOMES_MAX
        or any(
            not isinstance(item, str) or not item or "\x00" in item for item in value
        )
        or len(set(value)) != len(value)
    ):
        raise RouterError(code)
    for item in value:
        _utf8(item, code)
    return list(value)


def _request_bytes(
    details: Mapping[str, object], identity: Mapping[str, object], prompt: str
) -> bytes:
    body = {
        "format": REQUEST_FORMAT,
        **identity,
        "workspace": details["cwd"],
        "forge_version": FORGE_VERSION,
        "python_min": [3, 12],
        "backend": {
            "kind": "openai_compat",
            "base_url_env": details["base_url_env"],
            "auth": details["auth"],
            "model": details["model"],
        },
        "budgets": details["budgets"],
        "outcomes": details["outcomes"],
        "task_prompt": prompt,
        "task_prompt_sha256": digest(_utf8(prompt, "invalid_forge_prompt")),
    }
    return encoded(body) + b"\n"


def worker_argv(executable: str, root: str) -> tuple[str, ...]:
    return (
        executable,
        *PYTHON_ARGS,
        root + "/" + WORKER_NAME,
        root + "/" + REQUEST_NAME,
    )


def probe_argv(executable: str, root: str) -> tuple[str, ...]:
    return (executable, *PYTHON_ARGS, root + "/" + PROBE_NAME)


def _scripts(worker: bytes, probe: bytes) -> None:
    if (
        type(worker) is not bytes
        or type(probe) is not bytes
        or len(worker) > WORKER_SCRIPT_MAX
        or len(probe) > PROBE_SCRIPT_MAX
        or digest(worker) != WORKER_SHA256
        or digest(probe) != PROBE_SHA256
    ):
        raise RouterError("forge_control_digest_mismatch")


def plan(request: InvocationRequest) -> LaunchPlan:
    _contract(request.adapter_id, request.adapter_contract)
    if request.seam_input is not None:
        raise RouterError("forge_seam_input_unsupported")
    runtime, workflow, plane = (
        settings_layer(value)
        for value in (request.runtime, request.workflow, request.plane)
    )
    profile_path, profile_raw, parsed = binding(
        request.binding_config,
        request.files,
        request.environment_names,
    )
    prompt_source, task, overrides = node(request.node_config, request.files)
    if request.result_contract != "json-v1":
        raise RouterError("invalid_forge_result_contract")
    declared = outcomes(request.outcomes, "invalid_forge_result_contract")
    selected = resolve(
        ("profile", mapping(parsed["fallback"])),
        ("runtime", runtime),
        ("workflow", workflow),
        ("plane", plane),
        ("node", overrides),
    )
    worker, probe = packaged_scripts()
    _scripts(worker, probe)
    root = str(request.invocation_root)
    details: dict[str, object] = {
        "format": LAUNCH_FORMAT,
        "adapter": ADAPTER_ID,
        "contract": ADAPTER_CONTRACT,
        "invocation_id": request.invocation_id,
        "cwd": str(request.project_root),
        "profile_path": profile_path,
        "profile_sha256": digest(profile_raw),
        "prompt_source": prompt_source,
        "prompt_source_sha256": digest(task),
        "worker_path": root + "/" + WORKER_NAME,
        "worker_sha256": WORKER_SHA256,
        "probe_path": root + "/" + PROBE_NAME,
        "probe_sha256": PROBE_SHA256,
        "request_path": root + "/" + REQUEST_NAME,
        "request_sha256": "",
        "forge_version": FORGE_VERSION,
        "environment_names": list(request.environment_names),
        "base_url_env": parsed["base_url_env"],
        "auth": parsed["auth"],
        "model": mapping(selected["effective"])["model"],
        "budgets": parsed["budgets"],
        "result_contract": "json-v1",
        "outcomes": declared,
        "common_settings": dict(request.common_settings),
    }
    identity = {
        "invocation_id": request.invocation_id,
        "plane_id": request.plane_id,
        "node_id": request.node_id,
        "binding_id": request.binding_id,
        "producing_revision": request.producing_revision,
    }
    request_raw = _request_bytes(details, identity, prompt_text(task))
    if len(request_raw) > REQUEST_MAX:
        raise RouterError("forge_request_too_large")
    details["request_sha256"] = digest(request_raw)
    result = LaunchPlan(
        request.invocation_id,
        ADAPTER_ID,
        ADAPTER_CONTRACT,
        worker_argv(request.executable, root),
        {},
        {WORKER_NAME: worker, PROBE_NAME: probe, REQUEST_NAME: request_raw},
        selected,
        details,
    )
    launch_details(result)
    return result


def _hash(value: object, code: str = "invalid_launch_plan") -> str:
    if not isinstance(value, str) or not HEX64.fullmatch(value):
        raise RouterError(code)
    return value


def _absolute(value: object) -> str:
    result = text(value)
    path = PurePosixPath(result)
    if not path.is_absolute() or ".." in path.parts or str(path) != result:
        raise RouterError("invalid_launch_plan")
    return result


def launch_details(value: LaunchPlan) -> dict[str, object]:
    """Recheck the frozen projection from plan bytes and adapter constants only."""
    _contract(value.adapter_id, value.adapter_contract)
    try:
        obj = fields(value.details, DETAIL_FIELDS)
        root = str(PurePosixPath(_absolute(obj["worker_path"])).parent)
        _absolute(obj["cwd"])
        relative(obj["profile_path"])
        relative(obj["prompt_source"])
        for key in ("profile_sha256", "prompt_source_sha256", "request_sha256"):
            _hash(obj[key])
        names = obj["environment_names"]
        if not isinstance(names, list) or any(not isinstance(n, str) for n in names):
            raise RouterError("invalid_launch_plan")
        env_name(obj["base_url_env"], names)
        auth = mapping(obj["auth"])
        if auth.get("kind") == "env":
            fields(auth, {"kind", "name"})
            if env_name(auth["name"], names) == obj["base_url_env"]:
                raise RouterError("invalid_launch_plan")
        elif auth != {"kind": "none"}:
            raise RouterError("invalid_launch_plan")
        model_value(obj["model"])
        if (
            obj["format"] != LAUNCH_FORMAT
            or obj["adapter"] != ADAPTER_ID
            or obj["contract"] != ADAPTER_CONTRACT
            or obj["invocation_id"] != value.invocation_id
            or obj["worker_path"] != root + "/" + WORKER_NAME
            or obj["probe_path"] != root + "/" + PROBE_NAME
            or obj["request_path"] != root + "/" + REQUEST_NAME
            or obj["worker_sha256"] != WORKER_SHA256
            or obj["probe_sha256"] != PROBE_SHA256
            or obj["forge_version"] != FORGE_VERSION
            or obj["result_contract"] != "json-v1"
            or budgets(obj["budgets"]) != obj["budgets"]
            or outcomes(obj["outcomes"], "invalid_launch_plan") != obj["outcomes"]
        ):
            raise RouterError("invalid_launch_plan")
        mapping(obj["common_settings"])
        selected = fields(
            value.settings, {"requested", "effective", "provenance", "warnings"}
        )
        effective = mapping(selected["effective"])
        provenance = mapping(selected["provenance"])
        source = fields(provenance.get("model"), {"source", "policy"})
        if (
            effective != {"model": obj["model"]}
            or selected["requested"] != effective
            or selected["warnings"] != []
            or set(provenance) != {"model"}
            or source["source"] not in SOURCES
            or source["policy"] is not None
        ):
            raise RouterError("invalid_launch_plan")
        generated = value.generated_controls
        if (
            not value.argv
            or value.environment
            or set(generated) != {WORKER_NAME, PROBE_NAME, REQUEST_NAME}
            or value.argv != worker_argv(value.argv[0], root)
        ):
            raise RouterError("invalid_launch_plan")
        request_raw = generated[REQUEST_NAME]
        if (
            type(request_raw) is not bytes
            or len(request_raw) > REQUEST_MAX
            or sum(len(raw) for raw in generated.values()) > GENERATED_LIMIT
            or digest(request_raw) != obj["request_sha256"]
        ):
            raise RouterError("invalid_launch_plan")
        _scripts(generated[WORKER_NAME], generated[PROBE_NAME])
        native = fields(
            strict_json(request_raw),
            {
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
            },
        )
        prompt = text(native["task_prompt"])
        identity = {
            key: text(native[key])
            for key in (
                "invocation_id",
                "plane_id",
                "node_id",
                "binding_id",
                "producing_revision",
            )
        }
        if _request_bytes(obj, identity, prompt) != request_raw:
            raise RouterError("invalid_launch_plan")
        prompt_text(_utf8(prompt, "invalid_launch_plan"))
        if identity["invocation_id"] != value.invocation_id:
            raise RouterError("invalid_launch_plan")
    except RouterError as exc:
        if exc.code == "forge_control_digest_mismatch":
            raise
        raise RouterError("invalid_launch_plan") from exc
    except (KeyError, TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise RouterError("invalid_launch_plan") from exc
    return obj


def start_record(invocation_id: str, hard: float, soft: float) -> bytes:
    raw = json.dumps(
        {
            "format": START_FORMAT,
            "invocation_id": invocation_id,
            "process_deadline_unix": float(hard),
            "soft_deadline_unix": float(soft),
        },
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    if len(raw) > START_LIMIT:
        raise RouterError("invalid_launch_plan")
    return raw


# ---------------------------------------------------------------- probe grammar


def probe_line(report: Mapping[str, object]) -> bytes:
    """W-PR-01/02 canonical line for a grammar-valid report, in fixed key order."""
    if set(report) != set(PROBE_KEYS):
        raise RouterError("forge_probe_grammar")
    python, name = report["python"], report["implementation"]
    version, match = report["forge_guardrails"], report["files_match"]
    if (
        report["format"] != PROBE_FORMAT
        or not isinstance(python, str)
        or not PYTHON_GRAMMAR.fullmatch(python)
        or not isinstance(name, str)
        or not IMPLEMENTATION_GRAMMAR.fullmatch(name)
        or not (
            version is None
            or isinstance(version, str)
            and VERSION_GRAMMAR.fullmatch(version)
        )
        or type(match) is not bool
    ):
        raise RouterError("forge_probe_grammar")
    ordered = {key: report[key] for key in PROBE_KEYS}
    raw = (json.dumps(ordered, separators=(",", ":"), ensure_ascii=True) + "\n").encode(
        "ascii"
    )
    if len(raw) > PROBE_LIMIT:
        raise RouterError("forge_probe_grammar")
    return raw


def parse_probe(raw: bytes) -> dict[str, object] | None:
    """Return the grammar-valid report, or None for any grammar failure."""
    try:
        if len(raw) > PROBE_LIMIT or not raw.endswith(b"\n") or raw.count(b"\n") != 1:
            return None
        raw.decode("ascii")
        obj = mapping(strict_json(raw[:-1]))
        if tuple(obj) != PROBE_KEYS or probe_line(obj) != raw:
            return None
        return obj
    except (RouterError, UnicodeError):
        return None


def probe_accepted(report: Mapping[str, object]) -> bool:
    """W-PR-03 acceptance of a grammar-valid report."""
    major, minor, _ = (int(part) for part in str(report["python"]).split("."))
    return (
        major == 3
        and minor >= 12
        and report["implementation"] == "cpython"
        and report["forge_guardrails"] == FORGE_VERSION
        and report["files_match"] is True
    )


# ---------------------------------------------------------------- envelope


def envelope_line(
    invocation_id: str, status: str, semantic: str | None, code: str | None
) -> bytes:
    return (
        json.dumps(
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
        + "\n"
    ).encode("utf-8")


def parse_envelope(
    raw: bytes, invocation_id: str, exit_code: object
) -> tuple[dict[str, object], str | None]:
    """Return the retained envelope facts and, for ``completed``, the semantic."""
    facts: dict[str, object] = {
        "bytes": len(raw),
        "sha256": digest(raw),
        "parse": "malformed",
        "status": None,
        "worker_failure_code": None,
    }
    if not raw:
        facts["parse"] = "missing"
        return facts, None
    try:
        if (
            len(raw) > STDOUT_LIMIT
            or not raw.endswith(b"\n")
            or raw.count(b"\n") != 1
            or raw.startswith(b"\xef\xbb\xbf")
        ):
            return facts, None
        obj = mapping(strict_json(raw[:-1]))
        status, semantic, code = (
            obj.get("status"),
            obj.get("semantic"),
            obj.get("failure_code"),
        )
        if (
            tuple(obj) != ENVELOPE_KEYS
            or obj["format"] != ENVELOPE_FORMAT
            or not isinstance(obj["invocation_id"], str)
        ):
            return facts, None
        if status == "completed":
            if (
                not isinstance(semantic, str)
                or not semantic
                or code is not None
                or len(semantic.encode("utf-8")) > SEMANTIC_LIMIT
            ):
                return facts, None
        elif status == "failed":
            # B1: the type check precedes membership, so list/dict codes are
            # malformed (row 13) rather than an escaping TypeError.
            if (
                semantic is not None
                or not isinstance(code, str)
                or (code not in WORKER_CODES)
            ):
                return facts, None
        else:
            return facts, None
        if (
            envelope_line(
                cast(str, obj["invocation_id"]),
                status,
                cast("str | None", semantic),
                cast("str | None", code),
            )
            != raw
        ):
            return facts, None
    except (RouterError, UnicodeError, TypeError):
        return facts, None
    facts.update(status=status, worker_failure_code=code)
    if obj["invocation_id"] != invocation_id or exit_code != (
        0 if status == "completed" else 3
    ):
        facts["parse"] = "contradiction"
        return facts, None
    facts["parse"] = "ok"
    return facts, cast("str | None", semantic)


# ---------------------------------------------------------------- disposition


def clean(process: Mapping[str, object] | None) -> bool:
    return (
        process is not None
        and process["pid"] is not None
        and process["stop_cause"] is None
        and process["cleanup_confirmed"] is True
        and process["cleanup_forced"] is False
        and process["stdout_overflow"] is False
        and process["stderr_overflow"] is False
        and process["stdout_truncated"] is False
    )


def writer(*processes: Mapping[str, object] | None) -> str:
    """W-DS-01: stopped only if every process object contributes stopped."""
    for process in processes:
        if (
            process is not None
            and process["pid"] is not None
            and (
                process["stop_cause"] is not None
                or process["cleanup_forced"]
                or not process["cleanup_confirmed"]
            )
        ):
            return "may_live"
    return "stopped"


def probe_parsable(probe: Mapping[str, object]) -> bool:
    """W-PR-04: the line is parsed only for a clean probe, exit 0, no stderr."""
    return clean(probe) and probe["exit_code"] == 0 and probe["stderr_bytes"] == 0


def disposition(
    refusal: object,
    probe: Mapping[str, object] | None,
    report: Mapping[str, object] | None,
    main: Mapping[str, object] | None,
    envelope: Mapping[str, object] | None,
) -> tuple[str, str | None, str]:
    """W-DS-04 rows 1-17 from retained facts: transport, code and writer."""
    state = writer(probe, main)
    if refusal is not None:
        return "execution_failure", cast(str, refusal), state
    if probe is None:
        raise RouterError("completion_integrity")
    if probe["stop_cause"] == "cancelled":
        return "cancelled", "forge_cancelled", state
    if not probe_parsable(probe) or report is None:
        return "execution_failure", "forge_probe_failed", state
    if not probe_accepted(report):
        return "execution_failure", "forge_probe_mismatch", state
    if main is None:
        raise RouterError("completion_integrity")
    cause = main["stop_cause"]
    if cause == "cancelled":
        return "cancelled", "forge_cancelled", state
    if cause == "timeout":
        return "timeout", "forge_timeout", state
    if cause in {"stdout_overflow", "stderr_overflow"}:
        return (
            "protocol_failure",
            "forge_" + str(cause).replace("overflow", "limit"),
            state,
        )
    if cause is not None:
        return (
            "execution_failure",
            {
                "start_failed": "forge_spawn_failed",
                "unsupported_platform": "forge_unsupported_platform",
            }.get(str(cause), "forge_io_failed"),
            state,
        )
    if not clean(main):
        return "execution_failure", "forge_cleanup_unconfirmed", state
    if main["exit_code"] not in {0, 3}:
        return "execution_failure", "forge_nonzero_exit", state
    if envelope is None:
        raise RouterError("completion_integrity")
    parse = envelope["parse"]
    if parse == "missing":
        return "protocol_failure", "forge_envelope_missing", state
    if parse == "malformed":
        return "protocol_failure", "forge_envelope_malformed", state
    if parse == "contradiction":
        return "protocol_failure", "forge_envelope_contradiction", state
    if envelope["status"] == "completed":
        return "completed", None, state
    code = cast(str, envelope["worker_failure_code"])
    if code == "forge_deadline_exceeded":
        return "timeout", code, state
    return "execution_failure", code, state


# ---------------------------------------------------------------- result record


def observed(result: object) -> dict[str, object]:
    return {name: getattr(result, name) for name in PROCESS_FIELDS}


def seal(evidence: Mapping[str, object]) -> dict[str, object]:
    body = {k: v for k, v in evidence.items() if k != "receipt_sha256"}
    result = {**body, "receipt_sha256": digest(encoded(body))}
    if len(encoded(result)) > EVIDENCE_LIMIT:
        raise RouterError("completion_integrity")
    return result


def _process(value: object) -> dict[str, object]:
    obj = fields(value, set(PROCESS_FIELDS))
    if obj["pid"] is not None and (type(obj["pid"]) is not int or obj["pid"] <= 0):
        raise RouterError("completion_integrity")
    if obj["exit_code"] is not None and type(obj["exit_code"]) is not int:
        raise RouterError("completion_integrity")
    for key in ("stdout_bytes", "stderr_bytes"):
        if type(obj[key]) is not int or obj[key] < 0:
            raise RouterError("completion_integrity")
    for key in ("stdout_sha256", "stderr_sha256"):
        _hash(obj[key], "completion_integrity")
    for key in PROCESS_FIELDS[7:16] + ("cleanup_confirmed",):
        if type(obj[key]) is not bool:
            raise RouterError("completion_integrity")
    if obj["stop_cause"] not in STOP_CAUSES or obj["group_state"] not in {
        "closed",
        "present",
        "unknown",
    }:
        raise RouterError("completion_integrity")
    proven = (
        all(
            obj[k]
            for k in (
                "stdin_closed",
                "stdout_eof",
                "stderr_eof",
                "root_reaped",
            )
        )
        and obj["group_state"] == "closed"
    )
    if obj["pid"] is not None and obj["cleanup_confirmed"] != proven:
        raise RouterError("completion_integrity")
    if obj["pid"] is None and (
        obj["root_reaped"]
        or obj["cleanup_forced"]
        or obj["exit_code"] is not None
        or obj["cleanup_confirmed"] is not True
        or obj["stop_cause"] is None
    ):
        raise RouterError("completion_integrity")
    if obj["root_reaped"] != (obj["pid"] is not None and obj["exit_code"] is not None):
        raise RouterError("completion_integrity")
    return obj


def _start(value: object, invocation_id: str) -> None:
    obj = fields(value, {"process_deadline_unix", "soft_deadline_unix", "sha256"})
    hard, soft = obj["process_deadline_unix"], obj["soft_deadline_unix"]
    if (
        type(hard) is not float
        or type(soft) is not float
        or not math.isfinite(hard)
        or not math.isfinite(soft)
        or abs((hard - SOFT_RESERVE) - soft) > 1e-6
        or obj["sha256"] != digest(start_record(invocation_id, hard, soft))
    ):
        raise RouterError("completion_integrity")


def validate_completion(plan: LaunchPlan, completion: CompletionFacts) -> None:
    """W-RV-02 over plan, completion and adapter constants only."""
    try:
        details = launch_details(plan)  # 1
        e = fields(completion.evidence, EVIDENCE_FIELDS)  # 2
        if (
            e != seal(e)
            or e["format"] != RESULT_FORMAT
            or e["launch_sha256"] != digest(encoded(details))
        ):
            raise RouterError("completion_integrity")
        if (
            e["invocation_id"] != plan.invocation_id  # 3
            or completion.invocation_id != plan.invocation_id
            or completion.origin != "adapter"
        ):
            raise RouterError("completion_integrity")
        probe = _process(e["probe"]) if e["probe"] is not None else None  # 5
        main = _process(e["main"]) if e["main"] is not None else None
        pid = e["pid"]  # 4
        if (
            completion.pid != pid
            or (
                pid is not None
                and (
                    type(pid) is not int
                    or pid <= 0
                    or main is None
                    or main["pid"] != pid
                )
            )
            or ((main is None or main["pid"] is None) and pid is not None)
        ):
            raise RouterError("completion_integrity")
        report = e["probe_report"]
        refusal = e["refusal"]
        if refusal not in REFUSALS | {None}:  # 6
            raise RouterError("completion_integrity")
        accepted = False
        if report is not None:
            # C2: re-apply W-PR-01/02, then bind (7), then classify W-PR-03.
            line = probe_line(mapping(report))
            if (
                probe is None
                or not probe_parsable(probe)
                or len(line) != probe["stdout_bytes"]
                or digest(line) != probe["stdout_sha256"]
            ):
                raise RouterError("completion_integrity")
            accepted = probe_accepted(mapping(report))
        if refusal == "forge_owned_process_unavailable":
            if any(e[key] is not None for key in ("probe", "probe_report", "main")):
                raise RouterError("completion_integrity")
        elif refusal == "forge_deadline_too_short":
            if main is not None or not ((probe is None and report is None) or accepted):
                raise RouterError("completion_integrity")
        elif probe is None:
            raise RouterError("completion_integrity")
        if (main is not None) != (e["start"] is not None) or (
            main is not None and (refusal is not None or not accepted)
        ):
            raise RouterError("completion_integrity")
        if e["start"] is not None:
            _start(e["start"], plan.invocation_id)
        envelope = e["envelope"]
        parsable = clean(main) and main is not None and main["exit_code"] in {0, 3}
        if (envelope is not None) != parsable:
            raise RouterError("completion_integrity")
        if envelope is not None:  # 8
            env = fields(
                envelope,
                {
                    "bytes",
                    "sha256",
                    "parse",
                    "status",
                    "worker_failure_code",
                },
            )
            assert main is not None
            # B2: exact integers within declared bounds before any equality.
            if (
                type(env["bytes"]) is not int
                or not 0 <= env["bytes"] <= STDOUT_LIMIT
                or type(main["stdout_bytes"]) is not int
                or main["stdout_bytes"] > STDOUT_LIMIT
            ):
                raise RouterError("completion_integrity")
            _hash(env["sha256"], "completion_integrity")
            if (
                env["bytes"] != main["stdout_bytes"]
                or env["sha256"] != main["stdout_sha256"]
                or env["parse"] not in {"ok", "missing", "malformed", "contradiction"}
                or (env["parse"] == "missing") != (env["bytes"] == 0)
            ):
                raise RouterError("completion_integrity")
            if env["parse"] in {"missing", "malformed"}:
                if env["status"] is not None or env["worker_failure_code"] is not None:
                    raise RouterError("completion_integrity")
            elif env["status"] == "completed":
                if env["worker_failure_code"] is not None:
                    raise RouterError("completion_integrity")
            elif (
                env["status"] != "failed"
                or not isinstance(env["worker_failure_code"], str)
                or env["worker_failure_code"] not in WORKER_CODES
            ):
                raise RouterError("completion_integrity")
            if env["parse"] == "ok":
                semantic = (
                    completion.stdout.decode("utf-8")
                    if env["status"] == "completed"
                    else None
                )
                rebuilt = envelope_line(
                    plan.invocation_id,
                    cast(str, env["status"]),
                    semantic,
                    cast("str | None", env["worker_failure_code"]),
                )
                if (
                    len(rebuilt) != env["bytes"]
                    or digest(rebuilt) != env["sha256"]
                    or (main["exit_code"] == 0) != (env["status"] == "completed")
                ):
                    raise RouterError("completion_integrity")
            envelope = env
        transport, code, state = disposition(  # 9
            refusal,
            probe,
            cast("Mapping[str, object] | None", report),
            main,
            envelope,
        )
        if (
            completion.transport,
            completion.diagnostic,
            e["failure_code"],
            completion.writer_state,
            completion.exit_code,
        ) != (
            transport,
            code or "",
            code,
            state,
            main["exit_code"] if main is not None else None,
        ):
            raise RouterError("completion_integrity")
        if transport == "completed":  # 10
            sem = fields(e["semantic"], {"size", "sha256"})
            if type(sem["size"]) is not int or not 1 <= sem["size"] <= SEMANTIC_LIMIT:
                raise RouterError("completion_integrity")
            if (
                sem
                != {"size": len(completion.stdout), "sha256": digest(completion.stdout)}
                or not completion.stdout
                or len(completion.stdout) > SEMANTIC_LIMIT
            ):
                raise RouterError("completion_integrity")
        elif completion.stdout or e["semantic"] is not None:
            raise RouterError("completion_integrity")
        expected = b"" if code is None else ("[forge] " + code).encode("ascii")
        if completion.stderr != expected:  # 11
            raise RouterError("completion_integrity")
    except (
        RouterError,
        AssertionError,
        KeyError,
        TypeError,
        ValueError,
        UnicodeError,
        RecursionError,
    ) as exc:
        raise RouterError("completion_integrity") from exc
