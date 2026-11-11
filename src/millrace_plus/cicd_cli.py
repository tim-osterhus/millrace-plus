"""`millrace-plus cicd ...`: thin operator commands over the CI/CD workflow home.

Only `init` needs Core (it registers the instance). Submission, status, cancel
and decisions read and write local records only.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

from .cicd_records import CicdError
from .local_intake import IntakeError


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise CicdError("usage_error")


def _parser() -> _Parser:
    parser = _Parser(prog="millrace-plus cicd")
    commands = parser.add_subparsers(dest="operation", required=True)
    for name in (
        "prepare",
        "init",
        "submit",
        "submit-report",
        "correlate",
        "status",
        "cancel",
        "decide",
        "candidate",
        "deliver",
        "inspect-delivery",
    ):
        child = commands.add_parser(name)
        child.add_argument("--workflow-home", required=True, type=Path)
        if name in {"prepare", "init"}:
            child.add_argument("--target", required=True, type=Path)
            child.add_argument("--config", required=True, type=Path)
            # Optional: the Core store defaults to <workflow-home>/state.
            child.add_argument("--state-home", type=Path)
            child.add_argument("--catalog-home", type=Path)
            child.add_argument("--authority", type=Path)
        elif name in {"deliver", "inspect-delivery"}:
            child.add_argument("--request", required=True, type=Path)
            child.add_argument("--result", required=True, type=Path)
            child.add_argument("--execution-home", required=True, type=Path)
            child.add_argument("--node", required=True)
            child.add_argument("--plane", default="main")
            child.add_argument("--script", required=True, type=Path)
        elif name == "candidate":
            child.add_argument(
                "--operation",
                dest="candidate_operation",
                required=True,
                choices=(
                    "export",
                    "prepare-base",
                    "import",
                    "project-role",
                    "check",
                    "review",
                    "verify",
                    "restore",
                ),
            )
            child.add_argument("--request", required=True, type=Path)
            child.add_argument("--result", required=True, type=Path)
            child.add_argument("--execution-home", required=True, type=Path)
            child.add_argument("--node", required=True)
            child.add_argument("--plane", default="main")
            child.add_argument("--script", required=True, type=Path)
        elif name == "submit-report":
            child.add_argument("--report", required=True, type=Path)
        elif name == "correlate":
            child.add_argument("--correlation", required=True, type=Path)
        elif name == "submit":
            child.add_argument("--event", required=True, type=Path)
        else:
            child.add_argument("--event", required=name != "status")
        if name in {"cancel", "decide"}:
            child.add_argument("--request-id", required=True)
        if name == "decide":
            # approve-publish is parsed so it can refuse explicitly in this slice.
            child.add_argument(
                "--decision",
                required=True,
                choices=(
                    "cancel",
                    "close",
                    "retry-checks",
                    "approve-publish",
                    "continue",
                    "approve_delivery",
                    "approve_rollback",
                ),
            )
            child.add_argument("--evidence", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    from .router import CoreContractError

    try:
        args = _parser().parse_args(sys.argv[1:] if argv is None else argv)
        if args.operation in {"prepare", "init"}:
            from .local_intake import strict_json

            raw = args.config.read_bytes()
            selected = strict_json(raw)
            is_v2 = (
                isinstance(selected, dict)
                and selected.get("format") == "millrace-plus-cicd-config/v2"
            )
            if args.operation == "prepare":
                if not is_v2:
                    raise CicdError("prepare_requires_v2")
                from .cicd_v2_runtime import prepare

                result = prepare(
                    args.workflow_home, args.target, args.config, args.state_home
                )
                print(json.dumps(result, ensure_ascii=False))
                return 0
            if is_v2:
                from .cicd_v2_runtime import initialize

                result = initialize(
                    args.workflow_home,
                    args.target,
                    args.config,
                    args.state_home,
                    authority_file=args.authority,
                    catalog_home=args.catalog_home,
                )
                print(json.dumps(result, ensure_ascii=False))
                return 0
            from .cicd_runtime import initialize

            result = initialize(
                args.workflow_home,
                args.target,
                args.config,
                args.state_home,
                catalog_home=args.catalog_home,
            )
        elif args.operation in {"deliver", "inspect-delivery"}:
            import os

            from .cicd_candidates import bind_invocation
            from .cicd_effects import operate_delivery, validate_request
            from .cicd_source import write_exact
            from .cicd_v2_runtime import Runtime, V2Home
            from .local_intake import canonical, strict_json

            home = V2Home(args.workflow_home)
            request = validate_request(strict_json(args.request.read_bytes()))
            inspecting = args.operation == "inspect-delivery"
            if inspecting != (request["operation"] == "inspect"):
                raise CicdError("effect_operation_mismatch")
            result_path = args.result.resolve()
            if (
                result_path == args.request.resolve()
                or result_path.is_relative_to(home.target)
            ):
                raise CicdError("invalid_result_path")
            invocation = bind_invocation(
                args.execution_home, args.plane, args.node,
                args.script, os.getpid(), Path.cwd(),
            )
            result = operate_delivery(
                Runtime(home, args.script.parent.parent), request, invocation
            )
            write_exact(result_path, canonical(result), "result_conflict")
        elif args.operation == "candidate":
            import os

            from .cicd_candidates import bind_invocation, operate_candidate
            from .cicd_source import write_exact
            from .cicd_v2_records import validate_candidate_request
            from .cicd_v2_runtime import V2Home
            from .local_intake import canonical, strict_json

            home = V2Home(args.workflow_home)
            request = validate_candidate_request(
                strict_json(args.request.read_bytes()),
                operation=args.candidate_operation,
            )
            if (
                args.result.absolute() == args.request.absolute()
                or args.result.absolute().is_relative_to(home.target)
            ):
                raise CicdError("invalid_result_path")
            invocation = bind_invocation(
                args.execution_home,
                args.plane,
                args.node,
                args.script,
                os.getpid(),
                Path.cwd(),
            )
            result = operate_candidate(
                home, request, args.script.parent.parent, invocation
            )
            write_exact(args.result, canonical(result), "result_conflict")
        else:
            if (args.workflow_home / "cicd-v2.json").exists():
                from .cicd_v2_runtime import V2Home
                from .local_intake import strict_json

                home = V2Home(args.workflow_home)
                if args.operation == "submit":
                    result = home.submit(args.event)
                elif args.operation == "submit-report":
                    result = home.submit_report(args.report)
                elif args.operation == "correlate":
                    result = home.correlate(args.correlation)
                elif args.operation == "status":
                    result = home.status(args.event)
                elif args.operation == "decide":
                    record = strict_json(args.evidence.read_bytes())
                    if (
                        not isinstance(record, dict)
                        or record.get("request_id") != args.request_id
                        or record.get("input_id") != args.event
                        or record.get("kind") != args.decision
                    ):
                        raise CicdError("decision_argument_mismatch")
                    result = home.decide(record)
                else:
                    from .cicd_v2_records import DECISION

                    state = home.load_state(args.event)
                    task = home.task(args.event)
                    basis = (
                        state["hold_ref"]["sha256"]
                        if state and state["hold_ref"]
                        else state["basis_sha256"]
                        if state and state["basis_sha256"]
                        else home.file_ref(
                            home.workspace / (args.event + ".input.json")
                        )["sha256"]
                    )
                    result = home.decide(
                        {
                            "format": DECISION,
                            "request_id": args.request_id,
                            "input_id": args.event,
                            "expected_state_version": task["version"]
                            if state is None
                            else state["version"],
                            "kind": "cancel",
                            "basis_sha256": basis,
                            "target_stage": None,
                            "changed_cause_ref": None,
                            "permission_ref": None,
                            "evidence_refs": [],
                        }
                    )
                print(json.dumps(result, ensure_ascii=False))
                return 0
            if args.operation in {"submit-report", "correlate"}:
                raise CicdError("feedback_requires_v2")
            from .cicd_runtime import Home

            home = Home(args.workflow_home)
            if args.operation == "submit":
                result = home.submit(args.event)
            elif args.operation == "status":
                result = home.status(args.event)
            elif args.operation == "cancel":
                result = home.cancel(args.event, args.request_id)
            else:
                result = home.decide(
                    args.event, args.decision, args.request_id, args.evidence
                )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (CicdError, IntakeError, CoreContractError) as exc:
        print(json.dumps({"code": exc.code, "message": exc.code}), file=sys.stderr)
        return 2
    except OSError:
        print(json.dumps({"code": "io_error", "message": "io_error"}), file=sys.stderr)
        return 2
    except Exception as exc:
        # Catalog/Core refusals carry a stable code; anything else is a defect.
        code = getattr(exc, "code", None)
        if not isinstance(code, str):
            raise
        print(json.dumps({"code": code, "message": code}), file=sys.stderr)
        return 2
