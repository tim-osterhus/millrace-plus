"""Pure authoring template for the LAD provider-wait graph routes (test-side only).

``provider_nodes(role, binding)`` emits exactly the seven nodes of the frozen
route table for one foreground native role. ``expand(base)`` applies it to the
pre-provider stock graph: only each foreground native ``on_error`` is rewired to
its classifier, each foreground native node gains the LAD bridge literal, and
the two provider control files are declared. Learning and every success and
``on_unknown`` edge are untouched. The shipped ``workflow.json`` must equal
``serialize(expand(base))``; nothing here runs at workflow runtime.

The node count follows the route table (7 x 11 = 77 added nodes); it is an
inventory, not an execution budget.
"""

from __future__ import annotations

import copy
import json

FOREGROUND_ROLES = (
    "recon",
    "planner",
    "manager",
    "builder",
    "checker",
    "fixer",
    "doublechecker",
    "troubleshooter",
    "consultant",
    "updater",
    "arbiter",
)
BRIDGE = {
    "context_path": "lad-state/native-context.json",
    "outbox_path": "lad-state/native-diagnostics",
}
CONTROL_FILES = ("context/provider-policy.json", "scripts/provider_wait.py")
TROUBLESHOOTER_OUTCOMES = (
    "PASS_REVIEW_REPAIRED",
    "PASS_PREREQUISITE_REPAIRED",
    "FINDINGS",
    "ERROR",
    "HUMAN_BLOCKED",
)
UNCERTAIN = {"pause": {"reason": "lad_reconciliation_required"}}
COMMAND_ERROR = {"pause": {"reason": "lad_deterministic_failure"}}
COMMAND_UNKNOWN = {"pause": {"reason": "lad_unknown_deterministic_outcome"}}


def _wait(role: str) -> dict:
    return {
        "pause": {
            "reason": "provider_wait_operator_return",
            "resume": f"provider_due_{role}",
        }
    }


def _human_pause(role: str) -> dict:
    return {
        "pause": {
            "reason": "provider_human_resolution_required",
            "resume": f"provider_due_{role}",
        }
    }


def _command(action: str, role: str, on: dict) -> dict:
    return {
        "invoke": {
            "binding": "command",
            "timeout_seconds": 90,
            "config": {
                "script": "scripts/provider_wait.py",
                "args": [action, role],
            },
        },
        "result": "json-v1",
        "outcomes": list(on),
        "on": on,
        "on_error": copy.deepcopy(COMMAND_ERROR),
        "on_unknown": copy.deepcopy(COMMAND_UNKNOWN),
    }


def provider_nodes(role: str, binding: str) -> dict[str, dict]:
    """The seven provider nodes for one foreground role; pure and deterministic.

    ``binding`` is the captured provider-policy ``troubleshooter_binding`` used
    by the one diagnosis node, which copies the stock Troubleshooter prompt,
    timeout, inputs and result contract.
    """
    if role not in FOREGROUND_ROLES:
        raise ValueError("provider_role_excluded")
    human = f"provider_human_{role}"
    return {
        f"classify_provider_{role}": _command(
            "classify",
            role,
            {
                "ORDINARY_ERROR": {"goto": f"prepare_{role}_replacement"},
                "PROVIDER_WAIT": _wait(role),
                "PROVIDER_HUMAN_REQUIRED": {"goto": human},
                "UNCERTAIN": copy.deepcopy(UNCERTAIN),
            },
        ),
        f"provider_due_{role}": _command(
            "due",
            role,
            {
                "PROVIDER_WAIT": _wait(role),
                "RETRY_READY": {"goto": f"prepare_provider_retry_{role}"},
                "DIAGNOSIS_READY": {"goto": f"prepare_provider_diagnosis_{role}"},
                "PROVIDER_HUMAN_REQUIRED": {"goto": human},
                "UNCERTAIN": copy.deepcopy(UNCERTAIN),
            },
        ),
        f"prepare_provider_retry_{role}": _command(
            "prepare-retry",
            role,
            {
                "READY": {"goto": role},
                "PROVIDER_HUMAN_REQUIRED": {"goto": human},
                "UNCERTAIN": copy.deepcopy(UNCERTAIN),
            },
        ),
        f"prepare_provider_diagnosis_{role}": _command(
            "prepare-diagnosis",
            role,
            {
                "READY": {"goto": f"provider_diagnosis_{role}"},
                "PROVIDER_HUMAN_REQUIRED": {"goto": human},
                "UNCERTAIN": copy.deepcopy(UNCERTAIN),
            },
        ),
        f"provider_diagnosis_{role}": {
            "invoke": {
                "binding": binding,
                "timeout_seconds": 1800,
                "config": {"prompt": "prompts/troubleshooter.md"},
                "inputs": ["lad-state/active-role.json"],
            },
            "result": "json-v1",
            "outcomes": list(TROUBLESHOOTER_OUTCOMES),
            "on": {
                outcome: {"goto": f"accept_provider_diagnosis_{role}"}
                for outcome in TROUBLESHOOTER_OUTCOMES
            },
            "on_error": {"goto": human},
            "on_unknown": {"goto": human},
        },
        f"accept_provider_diagnosis_{role}": _command(
            "accept-diagnosis",
            role,
            {
                "RETRY_READY": {"goto": f"provider_due_{role}"},
                "PROVIDER_HUMAN_REQUIRED": {"goto": human},
                "UNCERTAIN": copy.deepcopy(UNCERTAIN),
            },
        ),
        human: _command(
            "human",
            role,
            {
                "HUMAN_REQUIRED": _human_pause(role),
                "UNCERTAIN": copy.deepcopy(UNCERTAIN),
            },
        ),
    }


def expand(base: dict, *, binding: str = "claude") -> dict:
    """Deterministic provider expansion of the pre-provider stock LAD graph."""
    graph = copy.deepcopy(base)
    nodes = graph["planes"]["foreground"]["nodes"]
    expanded: dict[str, dict] = {}
    for name, node in nodes.items():
        expanded[name] = node
        if name not in FOREGROUND_ROLES:
            continue
        invoke = node["invoke"]
        if invoke["binding"] == "command" or node["on_error"] != {
            "goto": f"prepare_{name}_replacement"
        }:
            raise ValueError("unexpected_foreground_native_node")
        invoke["config"] = {**invoke["config"], "diagnostic_bridge": dict(BRIDGE)}
        node["on_error"] = {"goto": f"classify_provider_{name}"}
        for added, body in provider_nodes(name, binding).items():
            if added in nodes or added in expanded:
                raise ValueError("provider_node_collision")
            expanded[added] = body
    graph["planes"]["foreground"]["nodes"] = expanded
    for name in CONTROL_FILES:
        if name in graph["control_files"]:
            raise ValueError("provider_control_file_collision")
        graph["control_files"].append(name)
    return graph


def serialize(graph: dict) -> bytes:
    """The shipped byte form: two-space JSON plus one trailing newline."""
    return json.dumps(graph, indent=2).encode() + b"\n"


def targets(node: dict) -> list[str]:
    """Every goto/resume target referenced by one node."""
    found: list[str] = []
    actions = [*node["on"].values(), node["on_error"], node["on_unknown"]]
    for action in actions:
        if "goto" in action:
            found.append(action["goto"])
        pause = action.get("pause")
        if isinstance(pause, dict) and "resume" in pause:
            found.append(pause["resume"])
    return found
