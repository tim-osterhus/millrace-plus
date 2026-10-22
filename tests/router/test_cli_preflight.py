"""Complete argv preflight, including argparse's easy-to-miss option forms."""

from __future__ import annotations

import contextlib
import io
import json
from collections.abc import Callable
from pathlib import Path

import pytest

from millrace_plus.catalog import CatalogError
from millrace_plus.router import preflight


@pytest.mark.parametrize(
    "argv",
    [
        ["instance", "list"],
        ["instance", "inspect", "--instance", "x"],
        ["instance", "create", "--workflow", "w", "--project", "p"],
        ["instance", "config", "--instance", "x", "--settings", "{}"],
        ["start", "--instance", "x"],
        *[
            [op, "--instance", "x", "--run", "y"]
            for op in ["recover", "pause", "cancel", "resume", "submit"]
        ],
        ["accept", "--instance", "x", "--run", "y", "--candidate", "c"],
        [
            "retry",
            "--instance",
            "x",
            "--run",
            "y",
            "--plane",
            "main",
            "--invocation",
            "i",
            "--reason",
            "r",
        ],
        [
            "redirect-plane",
            "--instance",
            "x",
            "--run",
            "y",
            "--plane",
            "main",
            "--node",
            "n",
            "--reason",
            "r",
        ],
        [
            "reconcile",
            "--instance",
            "x",
            "--run",
            "y",
            "--plane",
            "main",
            "--invocation",
            "i",
            "--reason",
            "r",
            "--stopped",
        ],
        ["validate", "--instance", "x"],
        ["status", "--instance", "x", "--run", "y"],
        ["replay", "--state-home", "h", "--envelope", "{}"],
        [
            "release-project-hold",
            "--project",
            "p",
            "--hold",
            "h",
            "--owner-instance",
            "x",
            "--owner-run",
            "y",
            "--reason",
            "r",
            "--stopped",
        ],
        *[
            [op, "--instance", "x", "--run", "y", "--plane", "main"]
            for op in ("pause-plane", "resume-plane", "cancel-plane")
        ],
    ],
)
def test_complete_core_grammar_preflight_parity(argv: list[str]) -> None:
    assert preflight.parse_argv(argv) is not None
    assert preflight.parse_argv([*argv, "--bogus"]) is None
    assert preflight.parse_argv([*argv, "--help"]) is None


def test_parser_options_and_prefix(tmp_path: Path) -> None:
    args = preflight.parse_argv(
        [
            "--state-home",
            "one",
            "instance",
            "--state-home=two",
            "create",
            "--state-home",
            "three",
            "--work=w",
            "--work=last",
            "--proj=p",
        ]
    )
    assert args.state_home == Path("three") and args.workflow == Path("last")
    assert preflight.strip_catalog_prefix(["--catalog-home=x", "instance", "list"]) == (
        Path("x"),
        ["instance", "list"],
    )
    with pytest.raises(CatalogError):
        preflight.strip_catalog_prefix(
            ["--catalog-home=x", "--catalog-home=y", "start"]
        )


@pytest.mark.parametrize(
    "operation",
    [
        "pause-plane",
        "resume-plane",
        "cancel-plane",
        "redirect-plane",
        "retry",
        "reconcile",
    ],
)
def test_plane_operations_refuse_omitted_plane(operation: str) -> None:
    argv = [operation, "--instance", "x", "--run", "y"]
    if operation == "redirect-plane":
        argv += ["--node", "n", "--reason", "r"]
    if operation in {"retry", "reconcile"}:
        argv += ["--invocation", "i", "--reason", "r"]
    if operation == "reconcile":
        argv += ["--stopped"]
    assert preflight.parse_argv([*argv, "--plane", "alpha"]) is not None
    assert preflight.parse_argv(argv) is None
    assert (
        preflight.parse_argv(
            [
                "redirect",
                "--instance",
                "x",
                "--run",
                "y",
                "--node",
                "n",
                "--reason",
                "r",
            ]
        )
        is None
    )


@pytest.mark.parametrize(
    "argv",
    [
        ["--help"],
        ["instance", "--help"],
        ["instance", "create", "--help"],
        ["start"],
        ["bogus"],
        ["start", "--instance", "x", "--wat"],
        ["replay", "--state-home", "h", "--envelope", '{"operation":NaN}'],
    ],
)
def test_help_and_malformed_delegate_exact_core_output(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    from millrace.router import cli as core_cli

    from millrace_plus.router import cli as plus_cli

    def call(function: object) -> tuple[int, str, str]:
        try:
            code = function(argv)
        except SystemExit as exc:
            code = exc.code
        output = capsys.readouterr()
        return code, output.out, output.err

    assert call(core_cli.main) == call(plus_cli.main)


def test_all_18_forms_match_public_core_behavior(tmp_path: Path) -> None:
    """Retain the accepted test identity while proving all 21 v2 CLI forms."""
    root = tmp_path
    from millrace.router import cli as core

    from millrace_plus.router import cli as plus

    forms = [
        ["instance", "list"],
        ["instance", "inspect", "--instance", "x"],
        [
            "instance",
            "create",
            "--workflow",
            str(root / "missing/workflow.json"),
            "--project",
            str(root / "missing-project"),
        ],
        ["instance", "config", "--instance", "x", "--settings", "{}"],
        ["start", "--instance", "x"],
        *[
            [op, "--instance", "x", "--run", "y"]
            for op in ("recover", "pause", "cancel", "resume", "submit")
        ],
        ["accept", "--instance", "x", "--run", "y", "--candidate", "c"],
        [
            "retry",
            "--instance",
            "x",
            "--run",
            "y",
            "--plane",
            "main",
            "--invocation",
            "i",
            "--reason",
            "r",
        ],
        [
            "redirect-plane",
            "--instance",
            "x",
            "--run",
            "y",
            "--plane",
            "main",
            "--node",
            "n",
            "--reason",
            "r",
        ],
        [
            "reconcile",
            "--instance",
            "x",
            "--run",
            "y",
            "--plane",
            "main",
            "--invocation",
            "i",
            "--reason",
            "r",
            "--stopped",
        ],
        ["validate", "--instance", "x"],
        ["status", "--instance", "x", "--run", "y"],
        ["replay", "--envelope", "{}"],
        [
            "release-project-hold",
            "--project",
            str(root / "project"),
            "--hold",
            "h",
            "--owner-instance",
            "x",
            "--owner-run",
            "y",
            "--reason",
            "r",
            "--stopped",
        ],
    ]
    forms += [
        [op, "--instance", "x", "--run", "y", "--plane", "main"]
        for op in ("pause-plane", "resume-plane", "cancel-plane")
    ]
    mutators = {
        "start",
        "recover",
        "pause",
        "cancel",
        "resume",
        "submit",
        "accept",
        "retry",
        "redirect-plane",
        "pause-plane",
        "resume-plane",
        "cancel-plane",
        "reconcile",
        "release-project-hold",
    }

    def call(
        function: Callable[[list[str]], int], argv: list[str]
    ) -> tuple[object, list[object], str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code: object = function(argv)
            except SystemExit as exc:
                code = exc.code
        return (
            code,
            [json.loads(line) for line in out.getvalue().splitlines() if line],
            err.getvalue(),
        )

    results: list[dict[str, object]] = []
    for index, form in enumerate(forms):
        argv = [*form, "--state-home", str(root / "state")]
        mutation = form[0] in mutators or form[:2] in [
            ["instance", "create"],
            ["instance", "config"],
        ]
        if mutation:
            argv += ["--request-id", f"{index:032x}"]
            if form[0] not in {
                "pause",
                "cancel",
                "pause-plane",
                "cancel-plane",
                "release-project-hold",
            }:
                argv += ["--expected-state-version", "0"]
        expected, actual = call(core.main, argv), call(plus.main, argv)
        assert actual == expected, (argv, expected, actual)
        results.append({"argv": argv, "exit": actual[0], "matched": True})
    assert len(results) == 21
