"""Bounded public argv grammar mirror used only for Plus ownership preflight."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

from millrace_plus import catalog

from . import control

OPS = {
    "start": "start_run",
    "recover": "recover_run",
    "pause": "pause_run",
    "cancel": "cancel_run",
    "resume": "resume_run",
    "submit": "submit_revision",
    "accept": "accept_revision",
    "retry": "retry_invocation",
    "pause-plane": "pause_plane",
    "resume-plane": "resume_plane",
    "cancel-plane": "cancel_plane",
    "redirect-plane": "redirect_plane",
    "reconcile": "reconcile_invocation",
}
INSTANCE_OPS = {"start_run", "update_instance_config"}
STOPS = {"pause_run", "cancel_run", "pause_plane", "cancel_plane"}


class _Skip(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise _Skip()

    def exit(self, status: int = 0, message: str | None = None) -> NoReturn:
        raise _Skip()

    def _print_message(self, message: str | None, file: object = None) -> None:
        pass


def _home(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state-home", type=Path, default=argparse.SUPPRESS)


def _mutation(parser: argparse.ArgumentParser, *, version: bool = True) -> None:
    parser.add_argument("--request-id")
    if version:
        parser.add_argument("--expected-state-version", type=int)


def parse_argv(argv: Sequence[str]) -> argparse.Namespace | None:
    """Mirror every current Core parser level; invalid/help argv belongs to Core."""
    parser = _Parser(prog="millrace router")
    _home(parser)
    commands = parser.add_subparsers(dest="command", required=True)
    instance = commands.add_parser("instance")
    _home(instance)
    instance_commands = instance.add_subparsers(dest="instance_command", required=True)
    for name in ("create", "list", "inspect", "config"):
        child = instance_commands.add_parser(name)
        _home(child)
        if name in {"inspect", "config"}:
            child.add_argument("--instance", required=True)
        if name == "create":
            child.add_argument("--workflow", type=Path, required=True)
            child.add_argument("--project", type=Path, required=True)
            child.add_argument("--host-paths", default="{}", metavar="JSON")
        if name == "config":
            child.add_argument("--settings", required=True)
        if name in {"create", "config"}:
            _mutation(child)
    for name, op in OPS.items():
        child = commands.add_parser(name)
        _home(child)
        child.add_argument("--instance", required=True)
        if op not in INSTANCE_OPS:
            child.add_argument("--run", required=True)
        if name in {
            "pause-plane",
            "resume-plane",
            "cancel-plane",
            "redirect-plane",
            "retry",
            "reconcile",
        }:
            child.add_argument("--plane", required=True)
        _mutation(child, version=op not in STOPS)
        if name == "start":
            child.add_argument(
                "--edit-mode", choices=("locked", "live"), default="locked"
            )
        if name == "accept":
            child.add_argument("--candidate", required=True)
        if name in {"retry", "reconcile"}:
            child.add_argument("--invocation", required=True)
        if name == "redirect-plane":
            child.add_argument("--node", required=True)
        if name in {"retry", "reconcile", "redirect-plane"}:
            child.add_argument("--reason", required=True)
        if name == "reconcile":
            child.add_argument("--stopped", action="store_true", required=True)
    for name in ("validate", "status"):
        child = commands.add_parser(name)
        _home(child)
        child.add_argument("--instance", required=True)
        if name == "status":
            child.add_argument("--run", required=True)
        else:
            child.add_argument("--run")
    replay = commands.add_parser("replay")
    _home(replay)
    replay.add_argument("--envelope", required=True)
    release = commands.add_parser("release-project-hold")
    _home(release)
    release.add_argument("--project", type=Path, required=True)
    release.add_argument("--hold", required=True)
    release.add_argument("--owner-instance", required=True)
    release.add_argument("--owner-run", required=True)
    release.add_argument("--reason", required=True)
    release.add_argument("--stopped", action="store_true", required=True)
    _mutation(release, version=False)
    try:
        return parser.parse_args(argv)
    except _Skip:
        return None


def strip_catalog_prefix(argv: Sequence[str]) -> tuple[Path | None, list[str]]:
    remaining = list(argv)
    selected = None
    if remaining and remaining[0] == "--catalog-home":
        if len(remaining) < 2 or not remaining[1] or remaining[1].startswith("--"):
            raise catalog.CatalogError(
                "usage_error", "--catalog-home requires a nonempty path"
            )
        selected, remaining = Path(remaining[1]), remaining[2:]
    elif remaining and remaining[0].startswith("--catalog-home="):
        value = remaining[0].split("=", 1)[1]
        if not value:
            raise catalog.CatalogError(
                "usage_error", "--catalog-home requires a nonempty path"
            )
        selected, remaining = Path(value), remaining[1:]
    if (
        selected is not None
        and remaining
        and (
            remaining[0] == "--catalog-home"
            or remaining[0].startswith("--catalog-home=")
        )
    ):
        raise catalog.CatalogError(
            "usage_error", "Use --catalog-home at most once before Core arguments"
        )
    return selected, remaining


def guard_argv(argv: Sequence[str], catalog_home: Path | None) -> None:
    args = parse_argv(argv)
    if args is None:
        return
    home = getattr(args, "state_home", Path.home() / ".local/state/millrace-router")
    if args.command == "replay":
        if not hasattr(args, "state_home"):
            return
        try:
            request = catalog.strict_json(args.envelope)
        except catalog.CatalogError:
            return
        if isinstance(request, dict):
            control._guard_request(request, home, catalog_home)
    elif args.command == "instance" and args.instance_command == "create":
        catalog.check_ownership(args.workflow, args.project, catalog_home=catalog_home)
    elif args.command in OPS or (
        args.command == "instance" and args.instance_command == "config"
    ):
        op = (
            "update_instance_config"
            if args.command == "instance"
            else OPS[args.command]
        )
        control._guard(
            op, args.instance, home, getattr(args, "run", None), catalog_home
        )
