"""CLI decisions precede private state and native invocation."""

import sys
from types import SimpleNamespace

import pytest

from millrace_plus.demo import cli, discovery


@pytest.fixture
def command(monkeypatch):
    from millrace_plus.demo import coordinator

    calls = []
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: False))
    monkeypatch.setattr(
        sys,
        "stdout",
        SimpleNamespace(isatty=lambda: False, write=lambda _: None, flush=lambda: None),
    )
    monkeypatch.setattr(cli, "check_candidate", lambda: {"millrace-os": "0.23.1"})
    monkeypatch.setattr(
        coordinator,
        "run_demo",
        lambda options, versions: calls.append((options, versions)) or 0,
    )
    return calls


def test_nonterminal_default_selects_no_native(command, monkeypatch):
    monkeypatch.setattr(
        discovery, "select_native", lambda *a: pytest.fail("no selection")
    )
    assert cli.main(["--no-open"]) == 0
    assert command[0][0].native is None and not command[0][0].interactive


@pytest.mark.parametrize(
    "argv", [["--profile", "x"], ["--consent-native"], ["--bogus"]]
)
def test_usage_fails_before_coordinator(command, argv):
    assert cli.main(argv) == 2
    assert command == []


def test_nonterminal_native_requires_explicit_consent(command, monkeypatch):
    monkeypatch.setattr(discovery, "select_native", lambda *a: {"selected": True})
    monkeypatch.setattr(discovery, "consent_description", lambda *a, **k: "disclosure")
    assert cli.main(["--native", "claude_print"]) == 2
    assert command == []
    assert cli.main(["--native", "claude_print", "--consent-native", "--no-web"]) == 0
    assert command[0][0].native == {"selected": True} and command[0][0].no_web


def test_candidate_checks_contracts_without_equal_versions(monkeypatch):
    monkeypatch.setattr(cli.sys, "version_info", (3, 12))
    monkeypatch.setattr(
        cli.metadata,
        "version",
        lambda name: "0.23.1" if name == "millrace-os" else "0.23.2",
    )
    assert cli.check_candidate()["millrace-os"] == "0.23.1"


def test_missing_component_fails_before_state(command, monkeypatch):
    monkeypatch.setattr(
        cli, "check_candidate", lambda: (_ for _ in ()).throw(cli.DemoError("missing"))
    )
    assert cli.main([]) == 2
    assert not command
