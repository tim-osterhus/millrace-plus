"""Core-free catalog commands and an ownership preflight over the Core CLI."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

from millrace_plus import catalog

from . import CoreContractError, require_core


class _CatalogParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise catalog.CatalogError("usage_error", message)


def _catalog_main(argv: Sequence[str]) -> int:
    parser = _CatalogParser(prog="millrace-plus catalog")
    commands = parser.add_subparsers(dest="operation", required=True)
    for name in ("list", "init", "copy"):
        child = commands.add_parser(name)
        child.add_argument("--catalog-home", type=Path)
        if name == "init":
            child.add_argument("selector")
        if name == "copy":
            child.add_argument("source", type=Path)
        if name != "list":
            child.add_argument("destination", type=Path)
    home = commands.add_parser(
        "home", help="Show, set, or reset the saved catalog root"
    )
    home_commands = home.add_subparsers(dest="home_operation", required=True)
    home_commands.add_parser("show")
    home_commands.add_parser("set").add_argument("path", type=Path)
    home_commands.add_parser("reset")
    args = parser.parse_args(argv)
    result: object
    if args.operation == "home":
        if args.home_operation == "set":
            result = catalog.set_catalog_home(args.path)
        elif args.home_operation == "reset":
            result = catalog.reset_catalog_home()
        else:
            result = catalog.catalog_home_status()
    elif args.operation == "list":
        result = {"templates": catalog.list_templates(catalog_home=args.catalog_home)}
    elif args.operation == "init":
        result = catalog.init_template(
            args.selector, args.destination, catalog_home=args.catalog_home
        )
    else:
        result = catalog.copy_workflow(
            args.source, args.destination, catalog_home=args.catalog_home
        )
    print(json.dumps(result))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run public Core argv unchanged after the additional ownership check."""
    try:
        require_core()
        from millrace.router import cli as core_cli

        from .preflight import guard_argv, strip_catalog_prefix
        from .resolver import resolve_adapter

        home, args = strip_catalog_prefix(sys.argv[1:] if argv is None else argv)
        guard_argv(args, home)
        return core_cli.main(args, adapter_resolver=resolve_adapter)
    except (CoreContractError, catalog.CatalogError) as exc:
        print(json.dumps({"code": exc.code, "message": str(exc)}), file=sys.stderr)
        return 2


def _subworkflow_main(argv: Sequence[str]) -> int:
    from millrace_plus import subworkflows
    from millrace_plus.subworkflow_records import SubworkflowError, _id

    parser = _CatalogParser(prog="millrace-plus subworkflow")
    commands = parser.add_subparsers(dest="operation", required=True)
    for name in ("call", "advance"):
        commands.add_parser(name).add_argument("--request", required=True, type=Path)
    for name in ("inspect", "cancel", "reconcile"):
        child = commands.add_parser(name)
        child.add_argument("--call-home", required=True, type=Path)
        child.add_argument("--call-key", required=True)
        if name == "cancel":
            child.add_argument("--request-id", required=True)
    args = parser.parse_args(argv)
    errors = (SubworkflowError,)
    try:
        if args.operation == "cancel":
            _id(args.request_id)
        ordinary_v1 = False
        if args.operation in {"inspect", "cancel", "reconcile"}:
            ordinary_v1 = not subworkflows._uses_v2(args.call_home, args.call_key)
            if ordinary_v1:
                path = subworkflows.Journal(args.call_home, args.call_key).path
                with path.open("rb") as stream:
                    retained = catalog.strict_json(stream.read(2097153))
                if (
                    isinstance(retained, dict)
                    and retained.get("format")
                    == "millrace-plus-lad-subworkflow-call/v1"
                ):
                    raise SubworkflowError("persistent_service_authority_required")
        if args.operation == "call" or ordinary_v1:
            require_core()
            from millrace.router.execution import RouterError

            errors = (SubworkflowError, RouterError)
        if args.operation == "call":
            result = subworkflows.call(args.request)
        elif args.operation == "advance":
            result = subworkflows.advance(args.request)
        elif args.operation == "inspect":
            result = subworkflows.inspect(args.call_home, args.call_key)
        elif args.operation == "cancel":
            result = subworkflows.cancel(args.call_home, args.call_key, args.request_id)
        else:
            result = subworkflows.reconcile(args.call_home, args.call_key)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except errors as exc:
        print(json.dumps({"code": exc.code, "message": str(exc)}), file=sys.stderr)
        return 2
    except OSError:
        print(json.dumps({"code": "io_error", "message": "io_error"}), file=sys.stderr)
        return 2


def _lad_main(argv: Sequence[str]) -> int:
    from millrace_plus.lad_service import Service
    from millrace_plus.local_intake import IntakeError

    parser = _CatalogParser(prog="millrace-plus lad")
    commands = parser.add_subparsers(dest="operation", required=True)
    for name in (
        "init",
        "submit",
        "status",
        "cancel",
        "decide",
        "stop",
        "parent-control",
    ):
        child = commands.add_parser(name)
        child.add_argument("--service-home", required=True, type=Path)
        if name == "init":
            for option in (
                "target",
                "os-profile",
                "worker-definition",
                "core-state-home",
            ):
                child.add_argument("--" + option, required=True, type=Path)
        elif name == "parent-control":
            child.add_argument("--request", required=True, type=Path)
        elif name == "submit":
            child.add_argument("--key", required=True)
            child.add_argument("--request", required=True, type=Path)
        elif name in {"status", "cancel", "decide"}:
            child.add_argument("--task", required=name != "status")
        if name in {"cancel", "decide", "stop"}:
            child.add_argument("--request-id", required=True)
        if name == "decide":
            child.add_argument(
                "--decision",
                required=True,
                choices=(
                    "retry-observation",
                    "continue",
                    "close-failed",
                    "acknowledge-failed",
                    "acknowledge-cancelled",
                ),
            )
            child.add_argument("--evidence", required=True, type=Path)
            child.add_argument("--resume-plane", choices=("foreground", "learning"))
    args = parser.parse_args(argv)
    if (
        args.operation == "decide"
        and args.resume_plane is not None
        and args.decision != "continue"
    ):
        parser.error("--resume-plane requires --decision continue")
    try:
        if args.operation == "init":
            from millrace_plus.lad_service_runtime import initialize

            result = initialize(
                args.service_home,
                args.target,
                args.os_profile,
                args.worker_definition,
                args.core_state_home,
            )
        else:
            service = Service(args.service_home)
        if args.operation == "init":
            pass
        elif args.operation == "submit":
            result = service.submit(args.key, args.request)
        elif args.operation == "cancel":
            result = service.cancel(args.task, args.request_id)
        elif args.operation in {"decide", "stop", "parent-control"}:
            from millrace_plus.lad_service_runtime import Runtime

            runtime = Runtime(service)
            if args.operation == "parent-control":
                result = runtime.parent_control(args.request)
            elif args.operation == "stop":
                result = runtime.stop(args.request_id)
            else:
                result = runtime.decide(
                    args.task,
                    args.decision,
                    args.request_id,
                    args.evidence,
                    resume_plane=args.resume_plane,
                )
        else:
            result = service.status(args.task)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (IntakeError, CoreContractError) as exc:
        print(json.dumps({"code": exc.code, "message": exc.code}), file=sys.stderr)
        return 2
    except OSError:
        print(json.dumps({"code": "io_error", "message": "io_error"}), file=sys.stderr)
        return 2
    except Exception as exc:
        if not isinstance(getattr(exc, "code", None), str):
            raise
        print(json.dumps({"code": exc.code, "message": exc.code}), file=sys.stderr)
        return 2


def _paperclip_main(argv: Sequence[str]) -> int:
    from millrace_plus.integrations import paperclip_bridge
    from millrace_plus.integrations.paperclip_records import BridgeError
    from millrace_plus.subworkflow_records import SubworkflowError

    parser = _CatalogParser(prog="millrace-plus paperclip")
    commands = parser.add_subparsers(dest="operation", required=True)
    for name in ("run", "inspect"):
        child = commands.add_parser(name)
        child.add_argument("--config", required=True, type=Path)
        child.add_argument("--assignment", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.operation == "inspect":
            result = paperclip_bridge.inspect(args.config, args.assignment)
            code = 0
        else:
            result = paperclip_bridge.run(args.config, args.assignment)
            code = paperclip_bridge.EXIT_CODES[str(result["status"])]
        print(json.dumps(result, ensure_ascii=False))
        return code
    except (BridgeError, SubworkflowError, catalog.CatalogError) as exc:
        print(json.dumps({"code": exc.code, "message": exc.code}), file=sys.stderr)
        return 5
    except OSError:
        print(json.dumps({"code": "io_error", "message": "io_error"}), file=sys.stderr)
        return 6


def cli() -> None:
    """Dispatch catalog without Core; retain the explicit router namespace."""
    args = sys.argv[1:]
    if args and args[0] == "lad":
        try:
            code = _lad_main(args[1:])
        except catalog.CatalogError as exc:
            print(json.dumps({"code": exc.code, "message": str(exc)}), file=sys.stderr)
            code = 2
        raise SystemExit(code)
    if args and args[0] == "paperclip":
        try:
            code = _paperclip_main(args[1:])
        except catalog.CatalogError as exc:
            print(json.dumps({"code": exc.code, "message": exc.code}), file=sys.stderr)
            code = 5
        raise SystemExit(code)
    if args and args[0] == "subworkflow":
        try:
            code = _subworkflow_main(args[1:])
        except (CoreContractError, catalog.CatalogError) as exc:
            print(json.dumps({"code": exc.code, "message": str(exc)}), file=sys.stderr)
            code = 2
        raise SystemExit(code)
    if args and args[0] == "catalog":
        try:
            code = _catalog_main(args[1:])
        except catalog.CatalogError as exc:
            print(json.dumps({"code": exc.code, "message": str(exc)}), file=sys.stderr)
            code = 2
        raise SystemExit(code)
    if not args or args[0] != "router":
        print(
            "usage: millrace-plus router ... | millrace-plus catalog ... | "
            "millrace-plus subworkflow ... | millrace-plus paperclip ... | "
            "millrace-plus lad ...",
            file=sys.stderr,
        )
        raise SystemExit(2)
    raise SystemExit(main(args[1:]))
