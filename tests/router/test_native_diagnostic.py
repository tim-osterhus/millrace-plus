"""Generic native diagnostic bridge: strict records and immutable publication.

Pure record and filesystem proofs only. Adapter integration lives with the
Claude and Codex adapter tests; nothing here starts a native process.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from millrace.router.execution import RouterError

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
