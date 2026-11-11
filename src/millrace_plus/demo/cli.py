"""Explicit full-component demo command; no native work without consent."""

from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Sequence
from importlib import metadata
from pathlib import Path
from typing import NoReturn

from .contracts import DemoError

__all__ = ["main", "check_candidate"]


def check_candidate() -> dict[str, str]:
    """Check installed component contracts, without inventing a release tuple."""
    if sys.version_info < (3, 12):
        raise DemoError("demo_requires_python_3_12")
    versions = {}
    try:
        for distribution in ("millrace-ai", "millrace-plus", "millrace-os"):
            versions[distribution] = metadata.version(distribution)
        requirements = {
            "millrace.router.execution": (
                "EXECUTION_CONTRACT",
                "millrace-router-execution/v2",
            ),
            "millrace.router.control": (
                "INSPECTION_CONTRACT",
                "millrace-router-inspection/v1",
            ),
            "millrace_plus.workflow_views": (
                "WORKFLOW_VIEWS_CONTRACT",
                "millrace-workflow-views/v1",
            ),
            "millrace_plus.workflow_os_client": (
                "CALL_FORMAT",
                "millrace-os-router-call/v2",
            ),
        }
        for name, (attribute, expected) in requirements.items():
            if getattr(importlib.import_module(name), attribute, None) != expected:
                raise DemoError("demo_component_contract_mismatch")
        for name, operations in {
            "millrace_plus.router.control": ("inspect_run_page",),
            "millrace_plus.workflow_views": ("publish_output", "read_output"),
            "millrace_os.demo_service": ("start_demo_service",),
        }.items():
            module = importlib.import_module(name)
            if any(
                not callable(getattr(module, operation, None))
                for operation in operations
            ):
                raise DemoError("demo_component_capability_missing")
    except (ImportError, metadata.PackageNotFoundError) as exc:
        raise DemoError("demo_requires_compatible_core_plus_os_bundle") from exc
    return versions


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise DemoError(message)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _Parser(
        prog="millrace demo", description="Run a private local hardware demo."
    )
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("millrace-demo-results"))
    parser.add_argument("--keep-demo", action="store_true")
    parser.add_argument("--native", metavar="ADAPTER")
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--consent-native", action="store_true")
    parser.add_argument("--no-web", action="store_true")
    try:
        args = parser.parse_args(argv)
        if args.profile is not None and args.native is None:
            raise DemoError("--profile requires --native")
        if args.consent_native and args.native is None:
            raise DemoError("--consent-native requires --native")
        interactive = bool(sys.stdin.isatty() and sys.stdout.isatty())
        from .discovery import consent_description, passive_inventory, select_native

        native = None
        if args.native:
            native = select_native(args.native, args.profile)
            if native is None:
                raise DemoError("native_configuration_required")
            print(consent_description(native, no_web=args.no_web), flush=True)
            if not args.consent_native:
                if not interactive:
                    raise DemoError(
                        "--native requires --consent-native without a terminal"
                    )
                if input(
                    "Allow this one native attempt? [y/N] "
                ).strip().lower() not in {"y", "yes"}:
                    raise DemoError("native_consent_required")
        elif interactive:
            for item in passive_inventory():
                print(str(item), flush=True)
            print(
                "Continue without a model (default). Select --native ADAPTER "
                "for an optional attempt.",
                flush=True,
            )
        versions = check_candidate()  # Before any demo root, service or invocation.
        from .coordinator import DemoOptions, run_demo

        return run_demo(
            DemoOptions(
                output=args.output,
                no_open=args.no_open,
                keep_demo=args.keep_demo,
                no_web=args.no_web,
                native=native,
                native_consent=native is not None,
                interactive=interactive,
            ),
            versions,
        )
    except DemoError as exc:
        print("millrace demo: " + str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
