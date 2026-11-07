"""Pure captured input and completion rules for the finite Millforge bridge."""

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

ADAPTER_ID = "millforge_run"
ADAPTER_CONTRACT = "millrace-millforge-run/v1"
LAUNCH_FORMAT = "millrace-millforge-launch/v1"
RESULT_FORMAT = "millrace-millforge-result/v1"
PROFILE_ROOT = "millforge-input/profile"
REQUEST_NAME = "millforge-input/request.json"
OUTPUT_NAME = "millforge-run"
GENERATED_LIMIT = 1114112
SEMANTIC_LIMIT = EVIDENCE_LIMIT = 65536
STDOUT_LIMIT = REQUEST_LIMIT = 8388608
STDERR_LIMIT = 1048576
STDERR_CAPTURE = 1024
RESERVE = 10
LIMIT_KEYS = {
    "model_requests", "tool_calls", "elapsed_seconds", "model_timeout_seconds",
    "tool_timeout_seconds", "input_bytes", "context_bytes", "output_bytes",
    "tool_output_bytes", "trace_bytes",
}
DETAIL_FIELDS = {
    "format", "profile_source", "profile_sha256", "profile_digest", "profile_id",
    "mode", "profile_root", "request_path", "output_path", "workspace",
    "outcomes", "auth_home", "environment_names", "common_settings",
    "request_sha256", "seam",
}
PROCESS_FIELDS = {
    "pid", "exit_code", "stop_cause", "stdout_bytes", "stderr_bytes",
    "stdout_sha256", "stderr_sha256", "stdout_truncated", "stderr_truncated",
    "stdout_overflow", "stderr_overflow", "stdin_closed", "stdout_eof",
    "stderr_eof", "root_reaped", "cleanup_forced", "group_state",
    "cleanup_confirmed",
}
EVIDENCE_FIELDS = {
    "format", "invocation_id", "pid", "main", "inspection", "accepted",
    "failure_code", "receipt_sha256",
}
ACCEPTED_FIELDS = {
    "request_id", "profile_id", "profile_digest", "mode", "status",
    "domain_outcome", "closure", "versions", "trace_records", "result_sha256",
    "capture_sha256", "saved_request_sha256", "request_value_sha256", "final",
}
FAILURES = {
    "millforge_insufficient_timeout", "millforge_output_exists",
    "millforge_owned_process_unavailable", "millforge_auth_home_unavailable",
    "millforge_cancelled", "millforge_timeout", "millforge_spawn_failed",
    "millforge_unsupported_platform", "millforge_io_failed",
    "millforge_cli_failed", "millforge_runtime_failed", "millforge_nonzero_exit",
    "millforge_stdout_limit", "millforge_stderr_limit",
    "millforge_cleanup_unconfirmed", "millforge_inspect_failed",
    "millforge_inspect_timeout", "millforge_record_invalid",
    "millforge_identity_mismatch", "millforge_semantic_invalid",
}


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def encoded(value: object) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise RouterError("invalid_millforge_input") from exc


def _hash(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch("[0-9a-f]{64}", value):
        raise RouterError("completion_integrity")
    return value


def _utf8(value: object) -> bytes:
    result = text(value)
    try:
        return result.encode("utf-8")
    except UnicodeError as exc:
        raise RouterError("invalid_millforge_input") from exc


def _absolute(value: object) -> str:
    result = text(value)
    path = PurePosixPath(result)
    if not path.is_absolute() or ".." in path.parts or str(path) != result:
        raise RouterError("invalid_millforge_path")
    return result


def _asset_path(value: object) -> str:
    result = relative(value)
    if ("\\" in result or re.match(r"^[A-Za-z]:", result)
            or any(part in {"", ".", ".."} for part in result.split("/"))
            or not result.endswith((".md", ".txt", ".json"))
            or result.split("/")[0].casefold() in {
                "profile.json", ".history", ".millforge-write.lock",
            }):
        raise RouterError("invalid_millforge_asset")
    _utf8(result)
    return result


def _empty(value: object) -> None:
    fields(value, set())


def _contract(adapter: str, contract: str) -> None:
    if (adapter, contract) != (ADAPTER_ID, ADAPTER_CONTRACT):
        raise RouterError("unsupported_adapter_contract")


def _control(files: Mapping[str, bytes], value: object) -> tuple[str, bytes]:
    path = relative(value)
    raw = files.get(path)
    if type(raw) is not bytes:
        raise RouterError("missing_control")
    return path, raw


def profile(raw: bytes, assets: Mapping[str, bytes]) -> dict[str, object]:
    """Check only bridge-required native facts; run owns full native admission."""
    obj = mapping(strict_json(raw))
    if obj.get("schema") != "millforge-profile/v2":
        raise RouterError("unsupported_millforge_profile")
    runtime = fields(obj.get("runtime"), {"pi_version", "binding_version"})
    if runtime != {"pi_version": "1.4.0", "binding_version": 2}:
        raise RouterError("unsupported_millforge_profile")
    name = text(obj.get("profile_id"))
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", name):
        raise RouterError("invalid_millforge_profile")
    if obj.get("mode") not in {"generate", "agent"}:
        raise RouterError("invalid_millforge_profile")
    entries = obj.get("assets")
    if not isinstance(entries, list) or len(entries) > 128:
        raise RouterError("invalid_millforge_asset")
    resources = []
    destinations = {PurePosixPath("profile.json")}
    names: set[str] = set()
    for entry in entries:
        item = fields(entry, {"path", "sha256", "kind"})
        path = _asset_path(item["path"])
        normalized = PurePosixPath(unicodedata.normalize("NFC", path).casefold())
        if path in names or any(
            normalized == old or normalized.is_relative_to(old)
            or old.is_relative_to(normalized) for old in destinations
        ):
            raise RouterError("invalid_millforge_asset")
        destinations.add(normalized)
        names.add(path)
        data = assets.get(path)
        if type(data) is not bytes or digest(data) != item["sha256"]:
            raise RouterError("invalid_millforge_asset")
        try:
            data.decode("utf-8")
        except UnicodeError as exc:
            raise RouterError("invalid_millforge_asset") from exc
        if item["kind"] not in {"context", "skill"}:
            raise RouterError("invalid_millforge_asset")
        resources.append({"path": path, "sha256": digest(data), "size": len(data)})
    if names != set(assets):
        raise RouterError("invalid_millforge_asset")
    limits = fields(obj.get("limits"), LIMIT_KEYS)
    for key, value in limits.items():
        number(value, integer=True, zero=key == "tool_calls")
        if cast(int, value) > 9007199254740991:
            raise RouterError("invalid_millforge_limits")
    elapsed = cast(int, limits["elapsed_seconds"])
    if not 6 <= elapsed <= 2147483 or any(
        cast(int, limits[key]) > elapsed - 5
        for key in ("model_timeout_seconds", "tool_timeout_seconds")
    ) or cast(int, limits["trace_bytes"]) <= 16384:
        raise RouterError("invalid_millforge_limits")
    terminal = fields(obj.get("terminal"), {
        "format", "required_keys", "outcome_key", "outcomes",
    })
    required, outcomes = terminal["required_keys"], terminal["outcomes"]
    if (terminal["format"] != "json-object" or terminal["outcome_key"] != "outcome"
            or not isinstance(required, list) or "outcome" not in required
            or any(not isinstance(k, str) for k in required)
            or any(k not in {"outcome", "summary", "refs"} for k in required)
            or len(set(required)) != len(required)
            or not isinstance(outcomes, list) or not outcomes
            or any(not isinstance(k, str) for k in outcomes)
            or len(set(outcomes)) != len(outcomes)):
        raise RouterError("invalid_millforge_terminal")
    model = mapping(obj.get("model"))
    auth = mapping(model.get("auth"))
    if auth.get("kind") == "oauth":
        fields(auth, {"kind", "provider"})
    elif auth.get("kind") == "env":
        fields(auth, {"kind", "name"})
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", text(auth["name"])):
            raise RouterError("invalid_millforge_auth")
    elif auth != {"kind": "none"}:
        raise RouterError("invalid_millforge_auth")
    manifest = {"profile_sha256": digest(raw), "assets": sorted(
        resources, key=lambda item: str(item["path"]).encode("utf-16-be"),
    )}
    return {"profile_id": name, "mode": obj["mode"], "limits": limits,
            "terminal": terminal, "auth": auth, "digest": digest(encoded(manifest))}


def binding(config: object, files: Mapping[str, bytes],
            host_paths: Mapping[str, str], environment_names: tuple[str, ...]
            ) -> tuple[str, bytes, dict[str, bytes], dict[str, object], str | None]:
    obj = fields(config, {"profile", "assets"}, {"auth_home"})
    path, raw = _control(files, obj["profile"])
    selected = mapping(obj["assets"])
    if any(not isinstance(value, str) for value in selected.values()):
        raise RouterError("invalid_millforge_asset")
    if len(set(selected.values())) != len(selected):
        raise RouterError("invalid_millforge_asset")
    assets = {name: _control(files, source)[1] for name, source in selected.items()}
    parsed = profile(raw, assets)
    auth = mapping(parsed["auth"])
    home: str | None = None
    if auth["kind"] == "oauth":
        alias = text(obj.get("auth_home"))
        if alias not in host_paths:
            raise RouterError("missing_host_path")
        home = _absolute(host_paths[alias])
    elif "auth_home" in obj:
        raise RouterError("invalid_millforge_auth")
    elif auth["kind"] == "env" and auth["name"] not in environment_names:
        raise RouterError("invalid_millforge_auth")
    return path, raw, assets, parsed, home


def _prompt(value: object, files: Mapping[str, bytes]) -> tuple[str, bytes]:
    name, raw = _control(files, fields(value, {"prompt"})["prompt"])
    try:
        decoded = raw.decode("utf-8")
        if not decoded.strip() or "\x00" in decoded:
            raise RouterError("invalid_millforge_prompt")
    except UnicodeError as exc:
        raise RouterError("invalid_millforge_prompt") from exc
    return name, raw


def validate_configuration(config: AdapterConfiguration) -> None:
    _contract(config.adapter_id, config.adapter_contract)
    for value in (config.runtime, config.workflow, *config.planes.values()):
        _empty(value)
    for value in config.bindings.values():
        binding(value, config.files, config.host_paths, config.environment_names)
    for (plane, _), (selected, value) in config.nodes.items():
        if selected not in config.bindings or plane not in config.planes:
            raise RouterError("invalid_adapter_settings")
        _prompt(value, config.files)


def _prefix(outcomes: tuple[str, ...]) -> str:
    return (
        "Return only one JSON object, without Markdown or commentary. "
        'Use {"outcome": <declared outcome>, "summary": <optional string>, '
        '"refs": <optional list of project-relative paths>}. Declared outcomes: '
        + json.dumps(list(outcomes), ensure_ascii=False) + ".\n\nTask:\n"
    )


def _timeout(common: object, elapsed: int) -> None:
    effective = mapping(mapping(common).get("effective"))
    timeout = number(effective.get("timeout_seconds"))
    if timeout < elapsed + RESERVE:
        raise RouterError("millforge_insufficient_timeout")


def plan(request: InvocationRequest) -> LaunchPlan:
    _contract(request.adapter_id, request.adapter_contract)
    for value in (request.runtime, request.workflow, request.plane):
        _empty(value)
    source, raw, assets, parsed, home = binding(
        request.binding_config, request.files, request.host_paths,
        request.environment_names,
    )
    _, prompt = _prompt(request.node_config, request.files)
    terminal = mapping(parsed["terminal"])
    if (request.result_contract != "json-v1" or not request.outcomes
            or set(cast(list[str], terminal["outcomes"])) != set(request.outcomes)):
        raise RouterError("invalid_millforge_terminal")
    _timeout(request.common_settings, cast(int, mapping(parsed["limits"])[
        "elapsed_seconds"]))
    context: list[str] = []
    seam = None
    if request.seam_input is not None:
        selected_seam = request.seam_input
        try:
            content = selected_seam.content.decode("utf-8")
        except UnicodeError as exc:
            raise RouterError("invalid_millforge_input") from exc
        if (digest(selected_seam.content) != selected_seam.sha256
                or selected_seam.path != request.invocation_root / "seam-input.json"):
            raise RouterError("invalid_millforge_input")
        context.append(content)
        seam = {"path": str(selected_seam.path), "sha256": selected_seam.sha256,
                "seam_id": selected_seam.seam_id, "sequence": selected_seam.sequence,
                "producer_invocation_id": selected_seam.producer_invocation_id}
    task = _prefix(request.outcomes) + prompt.decode("utf-8")
    if (len(_utf8(task)) > 1048576
            or any(len(_utf8(item)) > 1048576 for item in context)
            or len(_utf8(task)) + sum(len(_utf8(item)) for item in context)
            > cast(int, mapping(parsed["limits"])["input_bytes"])):
        raise RouterError("invalid_millforge_input")
    workspace = str(request.project_root) if parsed["mode"] == "agent" else None
    native_request = {
        "schema": "millforge-request/v1", "request_id": request.invocation_id,
        "profile_id": parsed["profile_id"], "profile_digest": parsed["digest"],
        "expected_mode": parsed["mode"], "task": task, "context": context,
        "workspace": workspace, "limits": {}, "correlation": {},
    }
    generated = {f"{PROFILE_ROOT}/profile.json": raw,
                 **{f"{PROFILE_ROOT}/{name}": data for name, data in assets.items()},
                 REQUEST_NAME: encoded(native_request) + b"\n"}
    if sum(map(len, generated.values())) > GENERATED_LIMIT:
        raise RouterError("invalid_launch_plan")
    root = request.invocation_root
    details = {
        "format": LAUNCH_FORMAT, "profile_source": source,
        "profile_sha256": digest(raw), "profile_digest": parsed["digest"],
        "profile_id": parsed["profile_id"], "mode": parsed["mode"],
        "profile_root": str(root / PROFILE_ROOT),
        "request_path": str(root / REQUEST_NAME),
        "output_path": str(root / OUTPUT_NAME), "workspace": workspace,
        "outcomes": list(request.outcomes), "auth_home": home,
        "environment_names": list(request.environment_names),
        "common_settings": dict(request.common_settings),
        "request_sha256": digest(generated[REQUEST_NAME]), "seam": seam,
    }
    argv: tuple[object, ...] = (
        request.executable, "run", "--profile", details["profile_root"],
        "--request", details["request_path"], "--output-dir", details["output_path"],
    )
    if home is not None:
        argv += ("--auth-home", home)
    result = LaunchPlan(
        request.invocation_id, ADAPTER_ID, ADAPTER_CONTRACT,
        cast(tuple[str, ...], argv),
        {}, generated,
        {"requested": {}, "effective": {}, "provenance": {}, "warnings": []}, details,
    )
    launch_details(result)
    return result


def launch_details(value: LaunchPlan) -> dict[str, object]:
    """Recheck the frozen projection without consulting current host resources."""
    _contract(value.adapter_id, value.adapter_contract)
    obj = fields(value.details, DETAIL_FIELDS)
    if (not value.argv or obj["format"] != LAUNCH_FORMAT
            or value.environment or value.settings != {
        "requested": {}, "effective": {}, "provenance": {}, "warnings": [],
    }):
        raise RouterError("invalid_launch_plan")
    root = PurePosixPath(_absolute(obj["output_path"])).parent
    if (obj["output_path"] != str(root / OUTPUT_NAME)
            or obj["profile_root"] != str(root / PROFILE_ROOT)
            or obj["request_path"] != str(root / REQUEST_NAME)):
        raise RouterError("invalid_launch_plan")
    relative(obj["profile_source"])
    generated = value.generated_controls
    profile_name = f"{PROFILE_ROOT}/profile.json"
    raw = generated[profile_name]
    assets = {name[len(PROFILE_ROOT) + 1:]: data for name, data in generated.items()
              if name.startswith(PROFILE_ROOT + "/") and name != profile_name}
    parsed = profile(raw, assets)
    if (set(generated) != {profile_name, REQUEST_NAME,
                          *(f"{PROFILE_ROOT}/{name}" for name in assets)}
            or sum(map(len, generated.values())) > GENERATED_LIMIT
            or digest(raw) != obj["profile_sha256"]
            or parsed["digest"] != obj["profile_digest"]
            or parsed["profile_id"] != obj["profile_id"]
            or parsed["mode"] != obj["mode"]
            or digest(generated[REQUEST_NAME]) != obj["request_sha256"]):
        raise RouterError("invalid_launch_plan")
    native = fields(strict_json(generated[REQUEST_NAME]), {
        "schema", "request_id", "profile_id", "profile_digest", "expected_mode",
        "task", "context", "workspace", "limits", "correlation",
    })
    outcomes = obj["outcomes"]
    if (not isinstance(outcomes, (list, tuple)) or not outcomes
            or any(not isinstance(item, str) for item in outcomes)
            or set(outcomes) != set(cast(list[str], mapping(parsed["terminal"])[
                "outcomes"]))
            or native["schema"] != "millforge-request/v1"
            or native["request_id"] != value.invocation_id
            or native["profile_id"] != parsed["profile_id"]
            or native["profile_digest"] != parsed["digest"]
            or native["expected_mode"] != parsed["mode"]
            or native["workspace"] != obj["workspace"]
            or native["limits"] != {} or native["correlation"] != {}
            or not text(native["task"]).startswith(_prefix(tuple(outcomes)))):
        raise RouterError("invalid_launch_plan")
    if parsed["mode"] == "agent":
        _absolute(native["workspace"])
    elif native["workspace"] is not None:
        raise RouterError("invalid_launch_plan")
    context = native["context"]
    if not isinstance(context, list):
        raise RouterError("invalid_launch_plan")
    if obj["seam"] is None:
        if context:
            raise RouterError("invalid_launch_plan")
    else:
        seam = fields(obj["seam"], {
            "path", "sha256", "seam_id", "sequence", "producer_invocation_id",
        })
        if (len(context) != 1 or not isinstance(context[0], str)
                or seam["path"] != str(root / "seam-input.json")
                or digest(_utf8(context[0])) != seam["sha256"]):
            raise RouterError("invalid_launch_plan")
        text(seam["seam_id"])
        number(seam["sequence"], integer=True)
        text(seam["producer_invocation_id"])
    if (len(_utf8(native["task"])) > 1048576
            or any(len(_utf8(item)) > 1048576 for item in context)
            or len(_utf8(native["task"])) + sum(len(_utf8(item)) for item in context)
            > cast(int, mapping(parsed["limits"])["input_bytes"])):
        raise RouterError("invalid_launch_plan")
    names = obj["environment_names"]
    if (not isinstance(names, (list, tuple))
            or any(not isinstance(n, str) for n in names)):
        raise RouterError("invalid_launch_plan")
    auth = mapping(parsed["auth"])
    home = obj["auth_home"]
    if auth["kind"] == "oauth":
        _absolute(home)
    elif home is not None or auth["kind"] == "env" and auth["name"] not in names:
        raise RouterError("invalid_launch_plan")
    argv: tuple[object, ...] = (
        value.argv[0], "run", "--profile", obj["profile_root"], "--request",
        obj["request_path"], "--output-dir", obj["output_path"],
    )
    if home is not None:
        argv += ("--auth-home", home)
    if value.argv != argv:
        raise RouterError("invalid_launch_plan")
    _timeout(obj["common_settings"], cast(int, mapping(parsed["limits"])[
        "elapsed_seconds"]))
    if max(len(encoded(obj)), len(encoded(value.settings))) > 1048576:
        raise RouterError("invalid_launch_plan")
    return {**obj, "request": native, "profile": parsed}


def semantic(output: object, outcome: object, outcomes: object) -> bytes:
    try:
        obj = fields(output, {"outcome"}, {"summary", "refs"})
        if obj["outcome"] != outcome or outcome not in cast(list[str], outcomes):
            raise RouterError("millforge_semantic_invalid")
        if "summary" in obj and not isinstance(obj["summary"], str):
            raise RouterError("millforge_semantic_invalid")
        if "refs" in obj:
            if not isinstance(obj["refs"], list):
                raise RouterError("millforge_semantic_invalid")
            for ref in obj["refs"]:
                relative(ref)
        raw = encoded(obj)
        if len(raw) > SEMANTIC_LIMIT:
            raise RouterError("millforge_semantic_invalid")
        return raw
    except (RouterError, TypeError, UnicodeError) as exc:
        raise RouterError("millforge_semantic_invalid") from exc


def closed(value: object, *, completed: bool) -> bool:
    obj = fields(value, {
        "worker", "tools", "writers", "inference", "detached_effects",
    })
    return (
        all(obj[key] == "closed" for key in ("worker", "tools", "writers"))
        and obj["detached_effects"] == "none_observed"
        and obj["inference"] in (
            {"response_returned"} if completed else
            {"response_returned", "disconnected_unknown"}
        )
    )


def seal(evidence: dict[str, object]) -> dict[str, object]:
    body = {k: v for k, v in evidence.items() if k != "receipt_sha256"}
    result = {**body, "receipt_sha256": digest(encoded(body))}
    if len(encoded(result)) > EVIDENCE_LIMIT:
        raise RouterError("completion_integrity")
    return result


def _process(value: object) -> dict[str, object]:
    obj = fields(value, PROCESS_FIELDS)
    if obj["pid"] is not None and (
        type(obj["pid"]) is not int or obj["pid"] <= 0
    ):
        raise RouterError("completion_integrity")
    if obj["exit_code"] is not None and type(obj["exit_code"]) is not int:
        raise RouterError("completion_integrity")
    for key in ("stdout_bytes", "stderr_bytes"):
        number(obj[key], integer=True, zero=True)
    for key in ("stdout_sha256", "stderr_sha256"):
        _hash(obj[key])
    for key in PROCESS_FIELDS - {
        "pid", "exit_code", "stop_cause", "stdout_bytes", "stderr_bytes",
        "stdout_sha256", "stderr_sha256", "group_state",
    }:
        if type(obj[key]) is not bool:
            raise RouterError("completion_integrity")
    if obj["stop_cause"] not in {
        None, "cancelled", "timeout", "start_failed", "unsupported_platform",
        "stdout_overflow", "stderr_overflow", "stdin_failed", "io_failed",
    } or obj["group_state"] not in {"closed", "present", "unknown"}:
        raise RouterError("completion_integrity")
    proven = all(obj[k] for k in (
        "stdin_closed", "stdout_eof", "stderr_eof", "root_reaped",
    )) and obj["group_state"] == "closed"
    if obj["pid"] is not None and obj["cleanup_confirmed"] != proven:
        raise RouterError("completion_integrity")
    if obj["pid"] is None and (obj["root_reaped"] or obj["cleanup_forced"]):
        raise RouterError("completion_integrity")
    if obj["root_reaped"] != (obj["pid"] is not None and obj["exit_code"] is not None):
        raise RouterError("completion_integrity")
    return obj


def clean_process(obj: Mapping[str, object]) -> bool:
    return (obj["pid"] is not None and obj["stop_cause"] is None
            and obj["cleanup_confirmed"] is True and obj["cleanup_forced"] is False
            and obj["stdout_overflow"] is False and obj["stderr_overflow"] is False
            and obj["stdout_truncated"] is False)


def disposition(obj: Mapping[str, object], *, inspection: bool = False
                ) -> tuple[str, str | None]:
    """One shared precedence rule for transport and historical fact validation."""
    cause = obj["stop_cause"]
    if cause == "cancelled":
        return "cancelled", "millforge_cancelled"
    if cause == "timeout":
        return ("timeout", "millforge_inspect_timeout" if inspection
                else "millforge_timeout")
    if cause in {"stdout_overflow", "stderr_overflow"}:
        return ("protocol_failure",
                "millforge_" + str(cause).replace("overflow", "limit"))
    if cause is not None:
        suffix = {
            "start_failed": "spawn_failed",
            "unsupported_platform": "unsupported_platform",
        }.get(str(cause), "io_failed")
        return "execution_failure", "millforge_" + suffix
    if not clean_process(obj):
        return "execution_failure", "millforge_cleanup_unconfirmed"
    code = obj["exit_code"]
    if inspection:
        return (("completed", None) if code == 0 else
                ("protocol_failure", "millforge_inspect_failed"))
    if code == 0:
        return "completed", None
    if code == 124:
        return "timeout", "millforge_timeout"
    if code == 130:
        return "cancelled", "millforge_cancelled"
    return "execution_failure", {
        2: "millforge_cli_failed", 3: "millforge_runtime_failed",
    }.get(cast(int, code), "millforge_nonzero_exit")


def validate_completion(plan: LaunchPlan, completion: CompletionFacts) -> None:
    """Check bounded retained facts without probing the CLI or current filesystem."""
    try:
        details = launch_details(plan)
        e = fields(completion.evidence, EVIDENCE_FIELDS)
        if (e != seal(e) or e["format"] != RESULT_FORMAT
                or e["invocation_id"] != plan.invocation_id
                or completion.invocation_id != plan.invocation_id
                or completion.origin != "adapter" or e["pid"] != completion.pid
                or completion.writer_state not in {"stopped", "may_live"}
                or e["failure_code"] not in FAILURES | {None}):
            raise RouterError("completion_integrity")
        main = _process(e["main"]) if e["main"] is not None else None
        inspected = _process(e["inspection"]) if e["inspection"] is not None else None
        if inspected is not None and (
            main is None or not clean_process(main) or main["exit_code"] not in {0, 3}
        ):
            raise RouterError("completion_integrity")
        if e["pid"] is not None and (main is None or main["pid"] != e["pid"]):
            raise RouterError("completion_integrity")
        if completion.exit_code != (main["exit_code"] if main is not None else None):
            raise RouterError("completion_integrity")
        accepted = e["accepted"]
        if accepted is not None:
            a = fields(accepted, ACCEPTED_FIELDS)
            native = mapping(details["request"])
            if (main is None or not clean_process(main)
                    or e["pid"] != main["pid"] or main["exit_code"] not in {0, 3}
                    or inspected is None or not clean_process(inspected)
                    or inspected["exit_code"] != 0
                    or any(a[key] != details[key] for key in (
                        "profile_id", "profile_digest", "mode"))
                    or a["request_id"] != plan.invocation_id
                    or a["request_value_sha256"] != digest(encoded(native))
                    or mapping(a["versions"]).get("millforge") != "0.2.0"
                    or mapping(a["versions"]).get("pi") != "1.4.0"):
                raise RouterError("completion_integrity")
            for key in ("result_sha256", "capture_sha256", "saved_request_sha256",
                        "request_value_sha256"):
                _hash(a[key])
            number(a["trace_records"], integer=True)
            success = a["status"] == "completed" and main["exit_code"] == 0
            failed = a["status"] == "failed" and main["exit_code"] == 3
            if not (success or failed):
                raise RouterError("completion_integrity")
            expected_writer = "stopped" if closed(
                a["closure"], completed=success,
            ) else "may_live"
            if completion.writer_state != expected_writer:
                raise RouterError("completion_integrity")
            if success:
                final = fields(a["final"], {"size", "sha256"})
                raw = semantic(strict_json(completion.stdout), a["domain_outcome"],
                               details["outcomes"])
                if completion.transport != "completed":
                    raise RouterError("completion_integrity")
                if (final != {"size": len(raw), "sha256": digest(raw)}
                        or completion.stdout != raw or e["failure_code"] is not None
                        or completion.writer_state != "stopped"
                        or completion.diagnostic):
                    raise RouterError("completion_integrity")
            elif (a["domain_outcome"] is not None or a["final"] is not None
                  or e["failure_code"] != "millforge_runtime_failed"):
                raise RouterError("completion_integrity")
        elif completion.transport == "completed":
            raise RouterError("completion_integrity")
        if completion.transport != "completed":
            if completion.stdout or completion.diagnostic != e["failure_code"]:
                raise RouterError("completion_integrity")
            if e["failure_code"] is None:
                raise RouterError("completion_integrity")
            if (main is not None and main["pid"] is not None
                    and accepted is None and completion.writer_state != "may_live"):
                raise RouterError("completion_integrity")
            if ((main is None or main["pid"] is None)
                    and completion.writer_state != "stopped"):
                raise RouterError("completion_integrity")
            if main is None:
                if (e["failure_code"] not in {
                    "millforge_insufficient_timeout", "millforge_output_exists",
                    "millforge_owned_process_unavailable",
                    "millforge_auth_home_unavailable",
                } or completion.transport != "execution_failure"
                        or inspected is not None):
                    raise RouterError("completion_integrity")
            else:
                transport, failure = disposition(main)
                if main["exit_code"] == 3 and clean_process(main):
                    pass  # Optional closure inspection never replaces the failure.
                elif transport == "completed":
                    if inspected is not None:
                        transport, failure = disposition(inspected, inspection=True)
                    if failure is None:
                        if e["failure_code"] not in {
                            "millforge_record_invalid", "millforge_identity_mismatch",
                            "millforge_semantic_invalid",
                            "millforge_cleanup_unconfirmed",
                            "millforge_cancelled", "millforge_inspect_timeout",
                        }:
                            raise RouterError("completion_integrity")
                        failure = e["failure_code"]
                        transport = {
                            "millforge_cancelled": "cancelled",
                            "millforge_inspect_timeout": "timeout",
                            "millforge_cleanup_unconfirmed": "execution_failure",
                        }.get(failure, "protocol_failure")
                if (completion.transport, e["failure_code"]) != (transport, failure):
                    raise RouterError("completion_integrity")
        expected_stderr = (
            ("[millforge] " + e["failure_code"]).encode()
            if e["failure_code"] else b""
        )
        if completion.stderr != expected_stderr:
            raise RouterError("completion_integrity")
    except (RouterError, KeyError, TypeError, ValueError, UnicodeError) as exc:
        raise RouterError("completion_integrity") from exc
