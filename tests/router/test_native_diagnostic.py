"""Generic native diagnostic bridge: strict records and immutable publication.

Pure record and filesystem proofs only. Adapter integration lives with the
Claude and Codex adapter tests; nothing here starts a native process.
"""

from __future__ import annotations

import ast
import errno
import hashlib
import json
import os
import re
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from millrace.router.execution import AdapterResult, LaunchPlan, RouterError

from millrace_plus.router import native_diagnostic as nd

BRIDGE = {
    "context_path": "state/native-context.json",
    "outbox_path": "state/native-diagnostics",
}
CONTEXT_ID = "00000000-0000-4000-8000-0000000000aa"
INVOCATION = "1" * 32


def context_bytes(**changes: Any) -> bytes:
    return json.dumps(
        {
            "format": "millrace-native-context/v1",
            "context_id": CONTEXT_ID,
            "input_sha256": "a" * 64,
            **changes,
        }
    ).encode()


def write_context(root: Path, raw: bytes | None = None) -> Path:
    path = root / BRIDGE["context_path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(context_bytes() if raw is None else raw)
    return path


def record(**changes: Any) -> nd.NativeDiagnostic:
    values: dict[str, Any] = {
        "context_id": CONTEXT_ID,
        "input_sha256": "a" * 64,
        "invocation_id": INVOCATION,
        "plane_id": "main",
        "node_id": "draft",
        "producing_revision": "revision",
        "adapter_id": "claude_print",
        "adapter_contract": "millrace-claude-print/v1",
        "evidence_format": "millrace-claude-print-result/v2",
        "launch_sha256": "b" * 64,
        "transport": "protocol_failure",
        "writer_state": "stopped",
        "failure_code": "claude_native_error",
        "protocol": nd.CLAUDE_PROTOCOL,
        "api_error_status": 429,
    }
    values.update(changes)
    return nd.NativeDiagnostic(**values)


def test_bridge_module_is_generic_and_adds_no_dependency() -> None:
    source = Path(nd.__file__).read_text()
    tree = ast.parse(source)
    imported = {
        alias.name if isinstance(node, ast.Import) else str(node.module)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imported <= {
        "__future__",
        "hashlib",
        "json",
        "os",
        "re",
        "stat",
        "tempfile",
        "collections.abc",
        "dataclasses",
        "pathlib",
        "typing",
        "None",
        "millrace.router.execution",
        "configuration",
    }
    # Code identifiers and literals (not prose docstrings) name no workflow layer.
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(
            node, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)
        )
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    tokens = [
        str(
            getattr(node, "id", None)
            or getattr(node, "attr", None)
            or getattr(node, "name", None)
            or getattr(node, "arg", None)
            or node.value
        )
        for node in ast.walk(tree)
        if isinstance(
            node,
            (ast.Name, ast.Attribute, ast.FunctionDef, ast.ClassDef, ast.arg),
        )
        or (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        )
    ]
    pattern = re.compile(
        r"(?i)(^|[^a-z])(lad|roles?|services?|troubleshooter)([^a-z]|$)"
    )
    assert not [token for token in tokens if pattern.search(token)]


@pytest.mark.parametrize(
    "value",
    [
        {"context_path": "a.json"},
        {**BRIDGE, "extra": "x"},
        {**BRIDGE, "context_path": "/abs/context.json"},
        {**BRIDGE, "context_path": "../context.json"},
        {**BRIDGE, "context_path": "./context.json"},
        {**BRIDGE, "context_path": "state//context.json"},
        {**BRIDGE, "outbox_path": "state/out/"},
        {**BRIDGE, "outbox_path": "state/./out"},
        {**BRIDGE, "outbox_path": "state\\out"},
        {**BRIDGE, "outbox_path": ""},
        {**BRIDGE, "outbox_path": 7},
        {**BRIDGE, "outbox_path": ".millrace-router-owner/out"},
        {**BRIDGE, "outbox_path": BRIDGE["context_path"]},
        {**BRIDGE, "outbox_path": "state/native-context.json/out"},
        {"context_path": "out/context.json", "outbox_path": "out"},
        None,
        [],
    ],
)
def test_bridge_config_is_strict_and_contained(value: object) -> None:
    with pytest.raises(RouterError, match="invalid_diagnostic_bridge|invalid_shape"):
        nd.BridgeConfig.load(value)


def test_bridge_config_roundtrip_and_record_path() -> None:
    bridge = nd.BridgeConfig.load(BRIDGE)
    assert bridge.to_json() == BRIDGE
    assert nd.node_bridge({"prompt": "task.txt"}) is None
    enabled = {"prompt": "task.txt", "diagnostic_bridge": BRIDGE}
    assert nd.node_bridge(enabled) == bridge
    with pytest.raises(RouterError):
        nd.node_bridge({**enabled, "diagnostic_bridge": None})
    expected = f"state/native-diagnostics/{CONTEXT_ID}/{INVOCATION}.json"
    assert bridge.record_path(CONTEXT_ID, INVOCATION) == expected


@pytest.mark.parametrize(
    "raw",
    [
        context_bytes(extra=1),
        context_bytes(format="millrace-native-context/v2"),
        context_bytes(context_id="../escape"),
        context_bytes(context_id="a/b"),
        context_bytes(context_id=""),
        context_bytes(context_id=7),
        context_bytes(input_sha256="A" * 64),
        context_bytes(input_sha256="a" * 63),
        b'{"format":"millrace-native-context/v1","format":"x"}',
        b"[]",
        b"\xff",
        b'{"value": NaN}',
    ],
)
def test_native_context_is_strict(raw: bytes) -> None:
    with pytest.raises(RouterError, match="bridge_context_invalid"):
        nd.NativeContext.load(raw)


def test_native_context_roundtrip() -> None:
    context = nd.NativeContext.load(context_bytes())
    assert context == nd.NativeContext(CONTEXT_ID, "a" * 64)
    assert nd.NativeContext.load(nd.canonical(context.to_json())) == context


@pytest.mark.parametrize(
    "value,expected",
    [
        (429, 429),
        (100, 100),
        (599, 599),
        (99, 99),
        (600, 600),
        (777, 777),
        (0, 0),
        (-1, -1),
        (True, None),
        (False, None),
        ("429", None),
        (429.0, None),
        (None, None),
        ([429], None),
    ],
)
def test_status_retains_any_non_bool_integer(value: object, expected: object) -> None:
    assert nd.typed_status(value) == expected


def test_record_canonical_roundtrip_and_exact_fields() -> None:
    value = record()
    raw = value.to_bytes()
    assert nd.NativeDiagnostic.load(raw) == value
    assert set(json.loads(raw)) == nd.RECORD_FIELDS
    assert json.loads(raw)["native_diagnostic"] == {
        "protocol": "claude-print-json/v1",
        "api_error_status": 429,
    }
    # The same facts in another byte form are not the published record.
    for other in (
        json.dumps(json.loads(raw), indent=1).encode(),
        json.dumps(json.loads(raw)).encode(),
        raw + b"\n",
    ):
        with pytest.raises(RouterError, match="invalid_native_diagnostic"):
            nd.NativeDiagnostic.load(other)


@pytest.mark.parametrize("status", [99, 600, 777, None])
def test_record_retains_unknown_integer_status_without_meaning(status: object) -> None:
    value = record(api_error_status=status)
    raw = value.to_bytes()
    assert json.loads(raw)["native_diagnostic"]["api_error_status"] == status
    assert nd.NativeDiagnostic.load(raw).api_error_status == status


@pytest.mark.parametrize(
    "changes",
    [
        {"api_error_status": True},
        {"api_error_status": "429"},
        {"api_error_status": 429.0},
        {"api_error_status": False},
        {"transport": "interrupted"},
        {"transport": ["completed"]},
        {"writer_state": "joined"},
        {"failure_code": "Bad Code"},
        {"failure_code": 7},
        {"context_id": "../x"},
        {"invocation_id": "a/b"},
        {"input_sha256": "x"},
        {"launch_sha256": None},
        {"plane_id": ""},
        {"node_id": None},
        {"protocol": None},
    ],
)
def test_record_rejects_hostile_fields(changes: dict[str, Any]) -> None:
    with pytest.raises(RouterError, match="invalid_native_diagnostic"):
        record(**changes)
    raw = json.loads(record().to_bytes())
    for key, value in changes.items():
        if key in nd.STATUS_FIELDS:
            raw["native_diagnostic"][key] = value
        else:
            raw[key] = value
    with pytest.raises(RouterError, match="invalid_native_diagnostic"):
        nd.NativeDiagnostic.load(nd.canonical(raw))


@pytest.mark.parametrize("member", ["extra", "missing", "status_extra", "format"])
def test_record_load_refuses_shape_drift(member: str) -> None:
    raw = json.loads(record().to_bytes())
    if member == "extra":
        raw["provider"] = "x"
    elif member == "missing":
        raw.pop("launch_sha256")
    elif member == "status_extra":
        raw["native_diagnostic"]["message"] = "rate limited"
    else:
        raw["format"] = "millrace-native-diagnostic/v2"
    with pytest.raises(RouterError, match="invalid_native_diagnostic"):
        nd.NativeDiagnostic.load(nd.canonical(raw))


@pytest.mark.parametrize("form", ["compact", "pretty_reordered", "trailing_newline"])
def test_preflight_reads_strict_context_beneath_cwd(tmp_path: Path, form: str) -> None:
    bridge = nd.BridgeConfig.load(BRIDGE)
    value = json.loads(context_bytes())
    reordered = dict(reversed(value.items()))
    raw = {
        "compact": nd.canonical(value),
        "pretty_reordered": json.dumps(reordered, indent=2).encode(),
        "trailing_newline": context_bytes() + b"\n",
    }[form]
    # Callers need no canonical form: strict keys and values are the contract.
    write_context(tmp_path, raw)
    assert nd.preflight(tmp_path, bridge) == nd.NativeContext(CONTEXT_ID, "a" * 64)
    # Preflight does not create the outbox; publication does.
    assert not (tmp_path / BRIDGE["outbox_path"]).exists()


@pytest.mark.parametrize(
    "case,code",
    [
        ("missing", "bridge_context_missing"),
        ("missing_parent", "bridge_context_missing"),
        ("invalid_json", "bridge_context_invalid"),
        ("extra_field", "bridge_context_invalid"),
        ("directory", "bridge_context_invalid"),
        ("symlink", "bridge_context_invalid"),
        ("parent_symlink", "bridge_context_invalid"),
        ("fifo", "bridge_context_invalid"),
        ("oversized", "bridge_context_invalid"),
        ("outbox_symlink", "bridge_outbox_invalid"),
        ("outbox_file", "bridge_outbox_invalid"),
    ],
)
def test_preflight_refusals_are_typed(tmp_path: Path, case: str, code: str) -> None:
    root = tmp_path / "cwd"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "native-context.json").write_bytes(context_bytes())
    bridge = nd.BridgeConfig.load(BRIDGE)
    path = root / BRIDGE["context_path"]
    if case not in {"missing_parent", "parent_symlink"}:
        path.parent.mkdir(parents=True)
    if case == "invalid_json":
        path.write_bytes(b"{")
    elif case == "extra_field":
        path.write_bytes(context_bytes(extra=True))
    elif case == "directory":
        path.mkdir()
    elif case == "symlink":
        path.symlink_to(outside / "native-context.json")
    elif case == "parent_symlink":
        path.parent.symlink_to(outside, target_is_directory=True)
    elif case == "fifo":
        os.mkfifo(path)
    elif case == "oversized":
        path.write_bytes(b" " * (nd.CONTEXT_LIMIT + 1))
    elif case.startswith("outbox"):
        write_context(root)
        outbox = root / BRIDGE["outbox_path"]
        if case == "outbox_symlink":
            outbox.symlink_to(outside, target_is_directory=True)
        else:
            outbox.write_text("not a directory")
    with pytest.raises(RouterError) as error:
        nd.preflight(root, bridge)
    assert error.value.code == code
    assert not (outside / CONTEXT_ID).exists()


def test_publication_replays_matching_and_refuses_conflict(tmp_path: Path) -> None:
    bridge = nd.BridgeConfig.load(BRIDGE)
    value = record()
    ref = nd.publish(tmp_path, bridge, value)
    path = tmp_path / ref.path
    raw = path.read_bytes()
    assert ref.path == bridge.record_path(CONTEXT_ID, INVOCATION)
    assert ref.sha256 == hashlib.sha256(raw).hexdigest()
    assert nd.publish(tmp_path, bridge, value) == ref
    assert sorted(p.name for p in path.parent.iterdir()) == [path.name]
    for other in (record(api_error_status=None), record(writer_state="may_live")):
        with pytest.raises(RouterError, match="native_diagnostic_conflict"):
            nd.publish(tmp_path, bridge, other)
        assert path.read_bytes() == raw
    assert sorted(p.name for p in path.parent.iterdir()) == [path.name]


@pytest.mark.parametrize("case", ["outbox_symlink", "context_dir_symlink", "record"])
def test_publication_never_follows_links(tmp_path: Path, case: str) -> None:
    root = tmp_path / "cwd"
    outside = tmp_path / "outside"
    outside.mkdir()
    bridge = nd.BridgeConfig.load(BRIDGE)
    outbox = root / BRIDGE["outbox_path"]
    if case == "outbox_symlink":
        outbox.parent.mkdir(parents=True)
        outbox.symlink_to(outside, target_is_directory=True)
    elif case == "context_dir_symlink":
        outbox.mkdir(parents=True)
        (outbox / CONTEXT_ID).symlink_to(outside, target_is_directory=True)
    else:
        (outbox / CONTEXT_ID).mkdir(parents=True)
        target = outside / "target.json"
        target.write_bytes(record().to_bytes())
        (outbox / CONTEXT_ID / f"{INVOCATION}.json").symlink_to(target)
    with pytest.raises((RouterError, OSError)):
        nd.publish(root, bridge, record())
    assert [p.name for p in outside.iterdir()] in ([], ["target.json"])


def test_verify_record_checks_bytes_path_and_expected_facts(tmp_path: Path) -> None:
    bridge = nd.BridgeConfig.load(BRIDGE)
    value = record()
    ref = nd.publish(tmp_path, bridge, value)
    raw = (tmp_path / ref.path).read_bytes()
    expected = {
        "context_id": CONTEXT_ID,
        "input_sha256": "a" * 64,
        "launch_sha256": "b" * 64,
        "native_diagnostic": {"protocol": nd.CLAUDE_PROTOCOL, "api_error_status": 429},
    }
    assert nd.verify_record(raw, ref, bridge, expected) == value
    other = nd.BridgeConfig.load({**BRIDGE, "outbox_path": "elsewhere"})
    moved = replace(ref, path=ref.path.replace(CONTEXT_ID, "other"))
    untyped = {"protocol": nd.CLAUDE_PROTOCOL, "api_error_status": None}
    hostile = (
        (raw + b" ", ref, bridge, expected),
        (raw, replace(ref, sha256="0" * 64), bridge, expected),
        (raw, moved, bridge, {}),
        (raw, ref, other, {}),
        (raw, ref, bridge, {"input_sha256": "c" * 64}),
        (raw, ref, bridge, {"not_a_field": 1}),
        (raw, ref, bridge, {"native_diagnostic": untyped}),
    )
    for args in hostile:
        with pytest.raises(RouterError):
            nd.verify_record(*args)


def test_evidence_links_are_pure_and_exact() -> None:
    bridge = nd.BridgeConfig.load(BRIDGE)
    path = bridge.record_path(CONTEXT_ID, INVOCATION)
    good = {
        "native_diagnostic": {"protocol": nd.CODEX_PROTOCOL, "api_error_status": None},
        "diagnostic_bridge": {"path": path, "sha256": "c" * 64},
    }
    kwargs: dict[str, Any] = {
        "bridge": bridge,
        "invocation_id": INVOCATION,
        "protocol": nd.CODEX_PROTOCOL,
    }
    expected = nd.DiagnosticBridgeRef(path, "c" * 64)
    assert nd.evidence_links(good, **kwargs) == (expected, None, True)
    unpublished = {**good, "diagnostic_bridge": None}
    assert nd.evidence_links(unpublished, **kwargs) == (None, None, True)
    refused = {"native_diagnostic": None, "diagnostic_bridge": None}
    assert nd.evidence_links(refused, **kwargs) == (None, None, False)
    # The generic link check retains any integer; adapters apply their own rules.
    unknown = {"protocol": nd.CODEX_PROTOCOL, "api_error_status": 777}
    retained = {**good, "native_diagnostic": unknown}
    assert nd.evidence_links(retained, **kwargs) == (expected, 777, True)
    outbox = BRIDGE["outbox_path"]
    statuses: list[object] = [
        None,
        {"protocol": nd.CLAUDE_PROTOCOL, "api_error_status": None},
        {"protocol": nd.CODEX_PROTOCOL},
        {"protocol": nd.CODEX_PROTOCOL, "api_error_status": True},
        {"protocol": nd.CODEX_PROTOCOL, "api_error_status": "429"},
        {"protocol": nd.CODEX_PROTOCOL, "api_error_status": None, "message": "x"},
    ]
    refs: list[object] = [
        {"path": path},
        {"path": path, "sha256": "C" * 64},
        {"path": path, "sha256": "c" * 64, "x": 1},
        {"path": path.replace(INVOCATION, "2" * 32), "sha256": "c" * 64},
        {"path": "elsewhere/" + path, "sha256": "c" * 64},
        {"path": f"{outbox}/../{INVOCATION}.json", "sha256": "c" * 64},
        {"path": f"{outbox}/a/b/{INVOCATION}.json", "sha256": "c" * 64},
        {"path": f"{outbox}//{INVOCATION}.json", "sha256": "c" * 64},
        {"path": 7, "sha256": "c" * 64},
        "ref",
    ]
    hostile = [{**good, "native_diagnostic": value} for value in statuses] + [
        {**good, "diagnostic_bridge": value} for value in refs
    ]
    for value in hostile:
        with pytest.raises(RouterError):
            nd.evidence_links(value, **kwargs)


# C1b millrace-native-diagnostic/v2: exact v1 fields plus seven binding facts.

V2_FORMAT = "millrace-native-diagnostic/v2"
NEW_FIELDS = {
    "settings_sha256",
    "observed_version",
    "requested_model",
    "requested_effort",
    "resolved_model",
    "resolved_effort",
    "host_route_sha256",
}
# Synthetic sentinels only: none may appear in any exported byte.
SENTINELS = (
    "/sentinel/private/bin/claude",
    "--sentinel-flag",
    "sentinel-env-value",
    "sentinel-provenance-source",
    "sentinel-warning",
    "/sentinel/private/native-home",
)


def sha(value: object) -> str:
    """Independent canonical digest (sorted, compact, UTF-8, finite)."""
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def record_v2(**changes: Any) -> nd.NativeDiagnosticV2:
    values: dict[str, Any] = {
        "diagnostic": record(),
        "settings_sha256": "c" * 64,
        "observed_version": "2.1.296",
        "requested_model": None,
        "requested_effort": "high",
        "resolved_model": None,
        "resolved_effort": "high",
        "host_route_sha256": "d" * 64,
    }
    values.update(changes)
    return nd.NativeDiagnosticV2(**values)


def settings(**effective: Any) -> dict[str, Any]:
    """Actual adapter plan.settings shape: top-level requested/effective/..."""
    return {
        "requested": dict(effective),
        "effective": dict(effective),
        "provenance": {
            key: {"source": "sentinel-provenance-source", "policy": None}
            for key in effective
        },
        "warnings": ["sentinel-warning"],
    }


def plan(**changes: Any) -> LaunchPlan:
    values: dict[str, Any] = {
        "invocation_id": INVOCATION,
        "adapter_id": "claude_print",
        "adapter_contract": "millrace-claude-print/v1",
        "argv": ("/sentinel/private/bin/claude", "-p", "--sentinel-flag"),
        "environment": {"SENTINEL": "sentinel-env-value"},
        "generated_controls": {"prompt.txt": b"Review."},
        "settings": settings(model="runtime-model", reasoning_effort="high"),
        "details": {
            "format": "launch/v2",
            "prompt_sha256": "e" * 64,
            "diagnostic_bridge": BRIDGE,
            "plane_id": "main",
            "node_id": "draft",
            "producing_revision": "revision",
        },
    }
    values.update(changes)
    return LaunchPlan(**values)


def terminal(**evidence: Any) -> AdapterResult:
    return AdapterResult(
        b"",
        b"",
        "protocol_failure",
        "stopped",
        "claude_native_error",
        1,
        {
            "format": "native-result/v1",
            "observed_version": "2.1.296",
            "failure_code": "claude_native_error",
            **evidence,
        },
    )


def export(
    root: Path, launch: LaunchPlan, result: AdapterResult, protocol: str
) -> AdapterResult:
    write_context(root)
    bridge = nd.BridgeConfig.load(BRIDGE)
    return nd.export_result(
        result,
        plan=launch,
        cwd=root,
        bridge=bridge,
        context=nd.preflight(root, bridge),
        evidence_format="native-result/v2",
        protocol=protocol,
        api_error_status=429,
    )


def test_v1_exact_bytes_reload_and_dispatch_are_unchanged() -> None:
    value = record()
    raw = value.to_bytes()
    assert raw == (
        b'{"adapter_contract":"millrace-claude-print/v1","adapter_id":"claude_print",'
        b'"context_id":"' + CONTEXT_ID.encode() + b'",'
        b'"evidence_format":"millrace-claude-print-result/v2",'
        b'"failure_code":"claude_native_error",'
        b'"format":"millrace-native-diagnostic/v1",'
        b'"input_sha256":"' + b"a" * 64 + b'",'
        b'"invocation_id":"' + INVOCATION.encode() + b'",'
        b'"launch_sha256":"' + b"b" * 64 + b'",'
        b'"native_diagnostic":{"api_error_status":429,'
        b'"protocol":"claude-print-json/v1"},'
        b'"node_id":"draft","plane_id":"main","producing_revision":"revision",'
        b'"transport":"protocol_failure","writer_state":"stopped"}'
    )
    assert nd.DIAGNOSTIC_FORMAT == "millrace-native-diagnostic/v1"
    loaded = nd.load_record(raw)
    # Dispatch returns the exact v1 type and never fills or upgrades v2 facts.
    assert type(loaded) is nd.NativeDiagnostic and loaded == value
    assert loaded.to_bytes() == raw and nd.NativeDiagnostic.load(raw) == value
    assert not NEW_FIELDS & set(json.loads(raw))
    with pytest.raises(RouterError, match="invalid_native_diagnostic"):
        nd.NativeDiagnosticV2.load(raw)


def test_v2_exact_fields_canonical_roundtrip_and_dispatch() -> None:
    value = record_v2()
    raw = value.to_bytes()
    obj = json.loads(raw)
    assert nd.BINDING_FIELDS == NEW_FIELDS
    assert set(obj) == nd.RECORD_V2_FIELDS == nd.RECORD_FIELDS | NEW_FIELDS
    assert obj["format"] == V2_FORMAT
    # Every v1 member keeps its v1 value; only the format string differs.
    v1 = json.loads(record().to_bytes())
    assert {k: obj[k] for k in nd.RECORD_FIELDS - {"format"}} == {
        k: v1[k] for k in nd.RECORD_FIELDS - {"format"}
    }
    assert nd.NativeDiagnosticV2.load(raw) == value
    loaded = nd.load_record(raw)
    assert type(loaded) is nd.NativeDiagnosticV2 and loaded == value
    assert not isinstance(loaded, nd.NativeDiagnostic)
    assert (loaded.context_id, loaded.invocation_id) == (CONTEXT_ID, INVOCATION)
    with pytest.raises(RouterError, match="invalid_native_diagnostic"):
        nd.NativeDiagnostic.load(raw)
    for other in (
        json.dumps(obj, indent=1).encode(),
        json.dumps(obj).encode(),
        raw + b"\n",
    ):
        for loader in (nd.NativeDiagnosticV2.load, nd.load_record):
            with pytest.raises(RouterError, match="invalid_native_diagnostic"):
                loader(other)


@pytest.mark.parametrize(
    "mutation",
    [
        *[("missing", key) for key in sorted(NEW_FIELDS)],
        ("extra", "provider_model"),
        ("v1_format", None),
        ("unknown_format", None),
        ("settings_sha256", "C" * 64),
        ("settings_sha256", None),
        ("host_route_sha256", "d" * 63),
        ("host_route_sha256", None),
        ("observed_version", 2),
        ("observed_version", ""),
        ("requested_model", True),
        ("requested_effort", ["high"]),
        ("resolved_model", ""),
        ("resolved_effort", {"value": "high"}),
        ("api_error_status", True),
        ("transport", "interrupted"),
        ("missing", "launch_sha256"),
    ],
)
def test_v2_strict_load_refuses_unknown_missing_and_wrong_fields(
    mutation: tuple[str, Any],
) -> None:
    key, value = mutation
    obj = json.loads(record_v2().to_bytes())
    if key == "missing":
        obj.pop(value)
    elif key == "extra":
        obj[value] = "x"
    elif key == "v1_format":
        obj["format"] = nd.DIAGNOSTIC_FORMAT
    elif key == "unknown_format":
        obj["format"] = "millrace-native-diagnostic/v3"
    elif key == "api_error_status":
        obj["native_diagnostic"]["api_error_status"] = value
    else:
        obj[key] = value
    raw = nd.canonical(obj)
    loaders = (nd.NativeDiagnosticV2.load, nd.load_record, nd.NativeDiagnostic.load)
    for loader in loaders:
        with pytest.raises(RouterError, match="invalid_native_diagnostic"):
            loader(raw)
    if key in NEW_FIELDS:
        with pytest.raises(RouterError, match="invalid_native_diagnostic"):
            record_v2(**{key: value})


@pytest.mark.parametrize(
    "raw",
    [
        b"{}",
        b"[]",
        b"\xff",
        b'{"format":"millrace-native-diagnostic/v1","format":"x"}',
        nd.canonical({**json.loads(record().to_bytes()), "format": None}),
        nd.canonical({**json.loads(record().to_bytes()), "format": "v2"}),
    ],
)
def test_load_record_refuses_unknown_or_absent_format(raw: bytes) -> None:
    with pytest.raises(RouterError, match="invalid_native_diagnostic"):
        nd.load_record(raw)


def test_v2_record_requires_an_exact_v1_part() -> None:
    with pytest.raises(RouterError, match="invalid_native_diagnostic"):
        record_v2(diagnostic=json.loads(record().to_bytes()))
    with pytest.raises(RouterError, match="invalid_native_diagnostic"):
        record_v2(diagnostic=record_v2())


def test_verify_record_dispatches_both_formats_and_v1_lacks_v2_facts(
    tmp_path: Path,
) -> None:
    bridge = nd.BridgeConfig.load(BRIDGE)
    old_root, new_root = tmp_path / "v1", tmp_path / "v2"
    old_root.mkdir()
    new_root.mkdir()
    old_ref = nd.publish(old_root, bridge, record())
    new_ref = nd.publish(new_root, bridge, record_v2())
    old_raw = (old_root / old_ref.path).read_bytes()
    new_raw = (new_root / new_ref.path).read_bytes()
    common = {"context_id": CONTEXT_ID, "launch_sha256": "b" * 64}
    assert nd.verify_record(old_raw, old_ref, bridge, common) == record()
    facts = {"settings_sha256": "c" * 64, "observed_version": "2.1.296"}
    assert nd.verify_record(new_raw, new_ref, bridge, {**common, **facts}) == (
        record_v2()
    )
    # A v1 record has no v2 authority facts; asking for one never matches.
    for key in NEW_FIELDS:
        with pytest.raises(RouterError, match="native_diagnostic_mismatch"):
            nd.verify_record(old_raw, old_ref, bridge, {key: None})
    for changed in (
        {"settings_sha256": "0" * 64},
        {"observed_version": "2.1.305"},
        {"requested_model": "other"},
        {"host_route_sha256": "0" * 64},
    ):
        with pytest.raises(RouterError, match="native_diagnostic_mismatch"):
            nd.verify_record(new_raw, new_ref, bridge, changed)


def test_settings_digest_is_actual_unwrapped_plan_settings() -> None:
    launch = plan()
    assert nd.settings_sha256(launch) == sha(launch.settings)
    # Core's separate launch_settings wrapper is never what is hashed.
    wrapper = {"adapter": launch.settings, "common": {"effective": {"timeout": 30}}}
    assert nd.settings_sha256(launch) != sha(wrapper)
    assert nd.settings_sha256(launch) != nd.launch_sha256(launch)
    # Provenance and warnings are part of the hashed actual settings.
    other = plan(settings={**launch.settings, "warnings": []})
    assert nd.settings_sha256(other) != nd.settings_sha256(launch)


def test_binding_facts_are_actual_values_and_null_only_when_absent() -> None:
    assert nd.binding_facts(plan()) == {
        "requested_model": "runtime-model",
        "requested_effort": "high",
        "resolved_model": "runtime-model",
        "resolved_effort": "high",
    }
    split = settings(model="node-model")
    split["requested"] = {"reasoning_effort": "low"}
    assert nd.binding_facts(plan(settings=split)) == {
        "requested_model": None,
        "requested_effort": "low",
        "resolved_model": "node-model",
        "resolved_effort": None,
    }
    assert set(nd.binding_facts(plan(settings=settings())).values()) == {None}
    wrapped = {"adapter": settings(model="m"), "common": {}}
    for hostile in (
        wrapped,
        {**settings(), "requested": None},
        {k: v for k, v in settings().items() if k != "effective"},
        settings(model=None),
        settings(model=7),
        settings(reasoning_effort=""),
    ):
        with pytest.raises(RouterError):
            nd.binding_facts(plan(settings=hostile))


def test_host_route_digest_is_exact_stable_and_route_sensitive() -> None:
    launch = plan()
    expected = sha(
        {
            "adapter_id": "claude_print",
            "adapter_contract": "millrace-claude-print/v1",
            "executable": "/sentinel/private/bin/claude",
            "native_home": None,
        }
    )
    assert nd.host_route_sha256(launch, nd.CLAUDE_PROTOCOL) == expected
    assert nd.HOST_ROUTE_FIELDS == {
        "adapter_id",
        "adapter_contract",
        "executable",
        "native_home",
    }
    occurrence = plan(
        invocation_id="2" * 32,
        argv=("/sentinel/private/bin/claude", "--other-prompt-flag"),
        generated_controls={"prompt.txt": b"Other."},
        details={**launch.details, "prompt_sha256": "f" * 64},
        settings=settings(model="other"),
    )
    assert nd.host_route_sha256(occurrence, nd.CLAUDE_PROTOCOL) == expected
    moved = plan(argv=("/other/claude",) + launch.argv[1:])
    assert nd.host_route_sha256(moved, nd.CLAUDE_PROTOCOL) != expected
    codex = plan(
        adapter_id="codex_exec",
        adapter_contract="millrace-codex-exec/v1",
        details={**launch.details, "codex_home": "/sentinel/private/native-home"},
    )
    route = nd.host_route_sha256(codex, nd.CODEX_PROTOCOL)
    assert route == sha(
        {
            "adapter_id": "codex_exec",
            "adapter_contract": "millrace-codex-exec/v1",
            "executable": "/sentinel/private/bin/claude",
            "native_home": "/sentinel/private/native-home",
        }
    )
    other_home = replace(
        codex, details={**codex.details, "codex_home": "/other/native-home"}
    )
    assert nd.host_route_sha256(other_home, nd.CODEX_PROTOCOL) != route
    for hostile in (
        replace(codex, details=launch.details),
        replace(codex, details={**codex.details, "codex_home": ""}),
        plan(argv=()),
        plan(argv=("",)),
    ):
        codex_route = hostile.adapter_id == "codex_exec"
        protocol = nd.CODEX_PROTOCOL if codex_route else nd.CLAUDE_PROTOCOL
        with pytest.raises(RouterError, match="invalid_native_diagnostic"):
            nd.host_route_sha256(hostile, protocol)


@pytest.mark.parametrize("observed", ["2.1.305", None])
def test_export_publishes_v2_actual_facts_without_private_values(
    tmp_path: Path, observed: str | None
) -> None:
    launch = plan()
    result = terminal(observed_version=observed)
    exported = export(tmp_path, launch, result, nd.CLAUDE_PROTOCOL)
    e = exported.adapter_evidence
    # The evidence gains exactly the existing v2 members; native facts unchanged.
    assert set(e) == set(result.adapter_evidence) | {
        "native_diagnostic",
        "diagnostic_bridge",
    }
    assert e["native_diagnostic"] == {
        "protocol": nd.CLAUDE_PROTOCOL,
        "api_error_status": 429,
    }
    assert (exported.classification, exported.writer_state) == (
        "protocol_failure",
        "stopped",
    )
    ref = nd.DiagnosticBridgeRef(**e["diagnostic_bridge"])
    raw = (tmp_path / ref.path).read_bytes()
    obj = json.loads(raw)
    assert set(obj) == nd.RECORD_V2_FIELDS and obj["format"] == V2_FORMAT
    assert {key: obj[key] for key in NEW_FIELDS} == {
        "settings_sha256": sha(launch.settings),
        "observed_version": observed,
        "requested_model": "runtime-model",
        "requested_effort": "high",
        "resolved_model": "runtime-model",
        "resolved_effort": "high",
        "host_route_sha256": nd.host_route_sha256(launch, nd.CLAUDE_PROTOCOL),
    }
    assert obj["launch_sha256"] == sha(launch.details)
    assert isinstance(nd.load_record(raw), nd.NativeDiagnosticV2)
    for text in (raw, nd.canonical(e)):
        for private in SENTINELS:
            assert private.encode() not in text
        for member in ("provenance", "warnings", "argv", "environment"):
            assert member.encode() not in text
    # The same actual facts replay to the same immutable record.
    again = export(tmp_path, launch, result, nd.CLAUDE_PROTOCOL)
    assert again.adapter_evidence == e


@pytest.mark.parametrize(
    "case", ["observed_type", "observed_empty", "model_type", "wrapped_settings"]
)
def test_export_refuses_malformed_actual_facts_as_unavailable(
    tmp_path: Path, case: str
) -> None:
    launch, result = plan(), terminal()
    if case == "observed_type":
        result = terminal(observed_version=2)
    elif case == "observed_empty":
        result = terminal(observed_version="")
    elif case == "model_type":
        launch = plan(settings=settings(model=7))
    else:
        launch = plan(settings={"adapter": settings(model="m"), "common": {}})
    exported = export(tmp_path, launch, result, nd.CLAUDE_PROTOCOL)
    e = exported.adapter_evidence
    # Native facts kept, null ref: never a guessed or nulled-out present value.
    assert e["diagnostic_bridge"] is None
    assert e["native_diagnostic"] == {
        "protocol": nd.CLAUDE_PROTOCOL,
        "api_error_status": 429,
    }
    assert (exported.stdout, exported.classification) == (b"", "protocol_failure")
    assert not (tmp_path / BRIDGE["outbox_path"]).exists()


def test_prior_v1_record_at_same_path_is_never_replaced_or_upgraded(
    tmp_path: Path,
) -> None:
    bridge = nd.BridgeConfig.load(BRIDGE)
    old = nd.publish(tmp_path, bridge, record())
    path = tmp_path / old.path
    before = path.read_bytes()
    exported = export(tmp_path, plan(), terminal(), nd.CLAUDE_PROTOCOL)
    assert exported.adapter_evidence["diagnostic_bridge"] is None
    assert exported.adapter_evidence["native_diagnostic"] is not None
    assert path.read_bytes() == before
    assert type(nd.load_record(before)) is nd.NativeDiagnostic
    assert sorted(p.name for p in path.parent.iterdir()) == [path.name]


@pytest.mark.parametrize("seam", ["mkstemp", "link", "fsync"])
def test_nonconflict_publication_oserror_keeps_native_facts_and_null_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seam: str
) -> None:
    def failed(*args: object, **kwargs: object) -> None:
        raise OSError(errno.EIO, "injected publication failure")

    target = {"mkstemp": tempfile, "link": os, "fsync": os}[seam]
    launch, result = plan(), terminal()
    with monkeypatch.context() as patch:
        patch.setattr(target, seam, failed)
        exported = export(tmp_path, launch, result, nd.CLAUDE_PROTOCOL)
    e = exported.adapter_evidence
    assert e["diagnostic_bridge"] is None
    assert e["native_diagnostic"] == {
        "protocol": nd.CLAUDE_PROTOCOL,
        "api_error_status": 429,
    }
    assert e["observed_version"] == "2.1.296" and e["format"] == "native-result/v2"
    assert (exported.classification, exported.writer_state, exported.exit_code) == (
        "protocol_failure",
        "stopped",
        1,
    )
    record_dir = tmp_path / BRIDGE["outbox_path"] / CONTEXT_ID
    # No record and no leaked temporary file remain at the immutable path.
    assert not record_dir.exists() or not list(record_dir.iterdir())
    # A later attempt with working I/O publishes normally.
    later = export(tmp_path, launch, result, nd.CLAUDE_PROTOCOL)
    assert later.adapter_evidence["diagnostic_bridge"] is not None


def test_read_uses_portable_flag_fallback_when_o_nonblock_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulated platform without O_NONBLOCK; not a native Windows claim."""
    path = write_context(tmp_path)
    flags: list[int] = []
    real = os.open

    def recording(file: Any, flag: int, *args: Any) -> int:
        flags.append(flag)
        return real(file, flag, *args)

    with monkeypatch.context() as patch:
        patch.delattr(os, "O_NONBLOCK")
        patch.setattr(os, "open", recording)
        assert nd._read(path, nd.CONTEXT_LIMIT) == context_bytes()
        bridge = nd.BridgeConfig.load(BRIDGE)
        assert nd.preflight(tmp_path, bridge).context_id == CONTEXT_ID
    assert flags and set(flags) == {os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)}
    # The link and regular-file refusals stay defined without the flag.
    (tmp_path / "dir").mkdir()
    with monkeypatch.context() as patch:
        patch.delattr(os, "O_NONBLOCK")
        with pytest.raises((RouterError, OSError)):
            nd._read(tmp_path / "dir", nd.CONTEXT_LIMIT)
