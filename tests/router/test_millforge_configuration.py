"""Captured Millforge inputs stay pure, bounded and independent of live state."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from millforge_support import configuration, native_profile, request
from millrace.router.execution import RouterError, SeamInput
from test_boundary import _no_live_access

from millrace_plus.router import millforge_configuration as c
from millrace_plus.router.millforge import MillforgeRunAdapter
from millrace_plus.router.resolver import resolve_adapter


def with_profile(req, profile):
    return replace(req, files={**req.files, "profile.json": c.encoded(profile)})


def test_exact_resolver_and_pure_snapshot(tmp_path, monkeypatch):
    adapter = resolve_adapter(c.ADAPTER_ID, c.ADAPTER_CONTRACT)
    assert isinstance(adapter, MillforgeRunAdapter)
    with pytest.raises(RouterError):
        resolve_adapter(c.ADAPTER_ID, "unsupported")
    req = request(tmp_path)
    with _no_live_access(monkeypatch):
        adapter.validate_configuration(configuration(req))
        first = adapter.plan(req)
        again = adapter.plan(req)
        moved = adapter.plan(replace(req, invocation_id="c" * 32,
                                     invocation_root=Path("/other/invocation")))
        c.launch_details(first)
    assert first == again
    assert first.settings == moved.settings
    assert first.environment == {}
    changed = replace(req, files={**req.files, "task.txt": b"A changed task"})
    assert first.generated_controls != c.plan(changed).generated_controls
    assert first.generated_controls == again.generated_controls


@pytest.mark.parametrize("mode", ["agent", "generate"])
def test_request_mode_workspace_and_negative_outcome_contract(tmp_path, mode):
    plan = c.plan(request(tmp_path, profile=native_profile(mode=mode)))
    body = json.loads(plan.generated_controls[c.REQUEST_NAME])
    assert body["expected_mode"] == mode
    assert body["workspace"] == (str(tmp_path) if mode == "agent" else None)
    assert body["context"] == [] and body["limits"] == {} and body["correlation"] == {}
    assert body["request_id"] == plan.invocation_id
    assert b"REJECT" in plan.generated_controls[c.REQUEST_NAME]
    assert c.semantic({"outcome": "REJECT"}, "REJECT", ["READY", "REJECT"])


@pytest.mark.parametrize("layer", ["runtime", "workflow", "plane", "node_config",
                                  "binding_config"])
def test_native_overrides_and_unknown_settings_refuse(tmp_path, layer):
    req = request(tmp_path)
    current = getattr(req, layer)
    bad = replace(req, **{layer: {**current, "model": "unselected"}})
    with pytest.raises(RouterError):
        c.validate_configuration(configuration(bad))
    with pytest.raises(RouterError):
        c.plan(bad)


@pytest.mark.parametrize("kind", ["none", "env", "oauth"])
def test_auth_references_are_explicit_and_shared_aliases_survive(tmp_path, kind):
    p = native_profile()
    auth = {"kind": kind}
    if kind == "env":
        auth["name"] = "DECLARED_KEY"
    if kind == "oauth":
        auth["provider"] = "openai"
    p["model"]["auth"] = auth
    req = request(tmp_path, profile=p, host_paths={"unrelated": "/other/adapter"})
    if kind == "env":
        with pytest.raises(RouterError):
            c.plan(req)
        req = replace(req, environment_names=("DECLARED_KEY",))
    if kind == "oauth":
        with pytest.raises(RouterError):
            c.plan(req)
        req = replace(req,
                      binding_config={**req.binding_config, "auth_home": "selected"},
                      host_paths={**req.host_paths, "selected": "/explicit/auth"})
    plan = c.plan(req)
    assert plan.details["auth_home"] == ("/explicit/auth" if kind == "oauth" else None)
    assert "DECLARED_KEY" not in json.dumps(plan.details) or kind == "env"
    if kind != "oauth":
        with pytest.raises(RouterError):
            c.plan(replace(req, binding_config={**req.binding_config,
                                                "auth_home": "x"}))


def test_native_digest_uses_raw_bytes_and_utf16_asset_order(tmp_path):
    p = native_profile()
    assets = {"\ue000.txt": b"bmp", "\U00010000.txt": "supplementary ✓".encode()}
    p["assets"] = [{"path": name, "kind": "context", "sha256": c.digest(raw)}
                   for name, raw in assets.items()]
    raw = c.encoded(p)
    selected = c.profile(raw, assets)
    manifest = {"assets": [{"path": name, "sha256": c.digest(assets[name]),
                            "size": len(assets[name])}
                           for name in ["\U00010000.txt", "\ue000.txt"]],
                "profile_sha256": hashlib.sha256(raw).hexdigest()}
    assert selected["digest"] == c.digest(c.encoded(manifest))
    assert c.profile(raw + b"\n", assets)["digest"] != selected["digest"]
    req = request(tmp_path, profile=p,
                  binding_config={"profile": "profile.json",
                                  "assets": {name: name for name in assets}},
                  files={"profile.json": raw, "task.txt": b"Review.", **assets})
    plan = c.plan(req)
    assert (plan.generated_controls[f"{c.PROFILE_ROOT}/\U00010000.txt"]
            == assets["\U00010000.txt"])


def test_digest_matches_installed_public_cli_unicode_vectors(tmp_path):
    selected = {name: os.environ.get(name) for name in (
        "MILLFORGE_M4_BIN", "MILLFORGE_M4_NODE",
    )}
    if not any(selected.values()):
        pytest.skip("public digest vectors require explicit Millforge and Node")
    assert all(selected.values()), selected
    cli = Path(selected["MILLFORGE_M4_BIN"])
    node = Path(selected["MILLFORGE_M4_NODE"])
    assert cli.is_absolute() and cli.is_file()
    assert node.is_absolute() and node.is_file()
    profile = native_profile()
    assets = {"\ue000.txt": b"BMP context",
              "\U00010000.txt": "astral context ✓".encode()}
    profile["assets"] = [
        {"path": name, "kind": "context", "sha256": c.digest(raw)}
        for name, raw in assets.items()
    ]
    # Both documents are valid; raw whitespace still changes the public digest.
    documents = [c.encoded(profile), c.encoded(profile) + b"\n"]
    digests = []
    for index, raw in enumerate(documents):
        directory = tmp_path / f"public-profile-{index}"
        directory.mkdir()
        (directory / "profile.json").write_bytes(raw)
        for name, data in assets.items():
            (directory / name).write_bytes(data)
        req = request(
            tmp_path, profile=profile,
            binding_config={"profile": "profile.json",
                            "assets": {name: name for name in assets}},
            files={"profile.json": raw, "task.txt": b"Review.", **assets},
        )
        bridge_digest = c.plan(req).details["profile_digest"]
        shown = subprocess.run(
            [str(node), str(cli), "profile", "show", str(directory)],
            cwd=tmp_path, env={"PATH": f"{node.parent}:/usr/bin:/bin",
                               "LANG": "C.UTF-8"},
            text=True, capture_output=True, timeout=10,
        )
        assert shown.returncode == 0, (shown.returncode, shown.stderr)
        public = json.loads(shown.stdout)
        assert public["profile"] == profile
        assert public["digest"] == bridge_digest
        digests.append(public["digest"])
    assert digests[0] != digests[1]


@pytest.mark.parametrize("names", [
    ["same.txt", "SAME.txt"], ["é.txt", "e\u0301.txt"],
    ["a.txt", "a.txt/nested.txt"], ["../escape.txt"], ["profile.json"],
])
def test_unsafe_overlapping_or_colliding_assets_refuse(names):
    p = native_profile()
    p["assets"] = [{"path": name, "kind": "context", "sha256": c.digest(b"asset")}
                   for name in names]
    with pytest.raises(RouterError):
        c.profile(c.encoded(p), dict.fromkeys(names, b"asset"))


def test_asset_mapping_missing_extra_hash_and_duplicate_source_refuse(tmp_path):
    req = request(tmp_path)
    for config in ({"profile": "profile.json", "assets": {"extra.txt": "task.txt"}},
                   {"profile": "profile.json", "assets": {"a.txt": "task.txt",
                                                           "b.txt": "task.txt"}}):
        with pytest.raises(RouterError):
            c.plan(replace(req, binding_config=config))
    p = native_profile()
    p["assets"] = [{"path": "a.txt", "kind": "context", "sha256": "f" * 64}]
    with pytest.raises(RouterError):
        c.profile(c.encoded(p), {"a.txt": b"changed"})


@pytest.mark.parametrize("mutation", [
    "schema", "terminal", "outcome", "timeout", "input",
])
def test_profile_terminal_timeout_and_input_bounds_refuse(tmp_path, mutation):
    p = native_profile()
    req = request(tmp_path)
    if mutation == "schema":
        p["schema"] = "millforge-profile/v1"
    if mutation == "terminal":
        p["terminal"]["format"] = "text"
    if mutation == "outcome":
        p["terminal"]["outcomes"] = ["READY"]
    if mutation == "timeout":
        req = replace(req, common_settings={"effective": {"timeout_seconds": 21}})
    if mutation == "input":
        p["limits"]["input_bytes"] = 1
    with pytest.raises(RouterError):
        c.plan(with_profile(req, p))


def test_generated_aggregate_counts_json_escaping(tmp_path):
    p = native_profile()
    p["limits"]["input_bytes"] = 1048576
    req = request(tmp_path, profile=p)
    # The raw task is below native input_bytes, but quotes double in request JSON.
    req = replace(req, files={**req.files, "task.txt": b'"' * 600000})
    with pytest.raises(RouterError, match="invalid_launch_plan"):
        c.plan(req)


def test_seam_context_is_exact_and_settings_do_not_depend_on_selection(
    tmp_path, monkeypatch,
):
    req = request(tmp_path)
    raw = '{"format":"millrace-seam-input/v1","data":"✓"}\n'.encode()
    seam = SeamInput(req.invocation_root / "seam-input.json", raw, c.digest(raw),
                     "handoff", 1, "c" * 32)
    with _no_live_access(monkeypatch):
        absent = c.plan(req)
        selected = c.plan(replace(req, seam_input=seam))
        assert absent.settings == selected.settings
        details = c.launch_details(selected)
    assert details["request"]["context"] == [raw.decode()]
    assert selected.details["seam"]["sha256"] == c.digest(raw)
    changed = replace(seam, content=raw + b" ", sha256=c.digest(raw + b" "))
    changed_plan = c.plan(replace(req, seam_input=changed))
    assert changed_plan.generated_controls != selected.generated_controls
    for bad in (replace(seam, sha256="f" * 64),
                replace(seam, content=b"\xff", sha256=c.digest(b"\xff"))):
        with pytest.raises(RouterError):
            c.plan(replace(req, seam_input=bad))


def test_duplicate_json_and_semantic_extra_keys_refuse(tmp_path):
    req = request(tmp_path)
    with pytest.raises(RouterError):
        c.plan(replace(req, files={**req.files,
                                   "profile.json": b'{"schema":1,"schema":2}'}))
    for obj in ({"outcome": "READY", "extra": 1}, {"outcome": "WRONG"},
                {"outcome": "READY", "summary": "x" * 65536}):
        with pytest.raises(RouterError, match="millforge_semantic_invalid"):
            c.semantic(obj, obj["outcome"], ["READY", "REJECT"])
