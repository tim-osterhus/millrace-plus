"""Real source/command-peer fixtures for the v2 public workflow."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from cicd_support import make_target, write_json
from test_cicd_v2_records import config

from millrace_plus import check_contracts as cc
from millrace_plus.cicd_v2_runtime import initialize, prepare


def initialized(
    root: Path,
    *,
    scenario: str = "repair",
    products: bool = False,
    guards: list[dict[str, object]] | None = None,
    environment_paths: list[str] | None = None,
    configure: Callable[[dict[str, object]], None] | None = None,
) -> dict[str, object]:
    target, head = make_target(root)
    value = config()
    value.update(
        target_root=str(target), repository="example/calc", local_authority_ref=None
    )
    policy = cc.stock_path_policy()
    if environment_paths:
        selected = cc.parse(policy)
        selected["rules"].append(
            {
                "surface": "environment_paths",
                "prefixes": environment_paths,
                "reason": "fixture prerequisite consumed by trusted check",
                "origin": "test operator",
                "override": "new capture",
            }
        )
        policy = cc.encode(selected)
    if products:
        selected_policy = cc.parse(policy)
        selected_policy["rules"].append(
            {
                "surface": "product_paths",
                "prefixes": ["build"],
                "reason": "fixture generated check evidence",
                "origin": "test operator",
                "override": "new capture",
            }
        )
        policy = cc.encode(selected_policy)
    (root / "path-policy.json").write_bytes(policy)
    policy_ref = cc.make_ref("path-policy.json", policy)
    value["path_policy_ref"] = policy_ref
    command = value["commands"][0]
    command.update(
        id="calc",
        argv=[sys.executable, "checks/check_calc.py"],
        path_policy_ref=policy_ref,
    )
    if products:
        command["expected_products"] = ["build"]
    value["commands"] = [command]
    value["check_policy"] = {
        "mandatory_ids": ["calc"],
        "path_rules": [{"prefix": "src", "command_ids": ["calc"]}],
        "shared_contract_paths": [],
        "full_ids": ["calc"],
    }
    value["investigation"] = {"probe_command_ids": ["calc"]}
    patterns = {
        "format": "millrace-plus-cicd-failure-patterns/v1",
        "patterns": [{"id": "assert", "stream": "stdout", "literal": "expected 5"}],
    }
    pattern_file = write_json(root / "failure-patterns.json", patterns)
    value["reproduction"]["command_ids"] = ["calc"]
    value["reproduction"]["failure_patterns_ref"] = cc.make_ref(
        "failure-patterns.json", pattern_file.read_bytes()
    )
    peer = root / "peer"
    peer_source = Path(__file__).parent / "fixtures/cicd_v2_peer.py"
    peer.write_text(
        f"#!{sys.executable}\nimport runpy\n"
        f"runpy.run_path({str(peer_source)!r}, run_name='__main__')\n"
    )
    peer.chmod(0o700)
    templates = Path(__file__).parents[2] / "src/millrace_plus/workflows/cicd-v2"
    for role, selection in [
        ("investigate", value["role_bindings"]["investigate"]),
        ("repair", value["role_bindings"]["repair"]),
        ("review", value["role_bindings"]["review"][0]),
    ]:
        selection["binding"]["executable"] = str(peer)
        for kind, key, suffix in [
            ("prompts", "prompt_ref", ".md"),
            ("skills", "skill_ref", ".md"),
            ("schemas", "result_schema_ref", ".json"),
        ]:
            name = f"{kind}/{role}{suffix}"
            destination = root / name
            destination.parent.mkdir(exist_ok=True)
            destination.write_bytes((templates / name).read_bytes())
            selection[key] = cc.make_ref(name, destination.read_bytes())
    if guards:
        value["resource_policy"]["guards"].extend(guards)
    if configure is not None:
        configure(value)
    config_file = write_json(root / "config.json", value)
    home = root / "home"
    prepared = prepare(home, target, config_file)
    result = initialize(
        home,
        target,
        config_file,
        authority_file=Path(prepared["suggested_authority_file"]),
    )
    return {
        "root": root,
        "target": target,
        "head": head,
        "home": home,
        "state": home / "state",
        "instance": result["instance_id"],
        "config": config_file,
        "scenario": scenario,
    }
