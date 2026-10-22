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


def cli() -> None:
    """Dispatch catalog without Core; retain the explicit router namespace."""
    args = sys.argv[1:]
    if args and args[0] == "catalog":
        try:
            code = _catalog_main(args[1:])
        except catalog.CatalogError as exc:
            print(json.dumps({"code": exc.code, "message": str(exc)}), file=sys.stderr)
            code = 2
        raise SystemExit(code)
    if not args or args[0] != "router":
        print(
            "usage: millrace-plus router ... | millrace-plus catalog ...",
            file=sys.stderr,
        )
        raise SystemExit(2)
    raise SystemExit(main(args[1:]))
