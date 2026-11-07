"""Plus control facade forwarding of Core's bounded inspection (UI1)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest
from millrace.router import control as core
from millrace.router.execution import RouterError
from plus_support import create_instance, native_flow, pi_instance, start_run

from millrace_plus.router import CoreContractError, control

MARKER = "millrace-router-inspection/v1"
FORWARDS = (
    "list_instances_page",
    "inspect_instance_page",
    "inspect_run_page",
    "inspect_revision_graph",
    "validate_revision_summary",
)


def _instance(flow: tuple[Path, Path, Path]) -> tuple[str, Path]:
    workflow, project, home = flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    return instance, home


def _calls(run: str = "b" * 32) -> list[tuple[str, tuple[Any, ...]]]:
    return [
        ("list_instances_page", ()),
        ("inspect_instance_page", ("a" * 32,)),
        ("inspect_run_page", ("a" * 32, run)),
        ("inspect_revision_graph", ("a" * 32, run, "c" * 64)),
        ("validate_revision_summary", ("a" * 32,)),
    ]


def test_exports_and_marker_match_core() -> None:
    assert set(FORWARDS) <= set(control.__all__)
    assert "INSPECTION_CONTRACT" not in control.__all__
    assert control.INSPECTION_CONTRACT == core.INSPECTION_CONTRACT == MARKER


@pytest.mark.parametrize(
    "operation,args,values",
    [
        ("list_instances_page", (), {"limit": 7, "cursor": "i" + "0" * 32 + "_x"}),
        ("inspect_instance_page", ("instance",), {"limit": 3, "cursor": None}),
        (
            "inspect_run_page",
            ("instance", "run"),
            {
                "section": "invocations",
                "limit": 2,
                "cursor": None,
                "request_id": None,
                "invocation_id": "1" * 32,
            },
        ),
        ("inspect_revision_graph", ("instance", "run", "f" * 64), {}),
        ("validate_revision_summary", ("instance",), {"run_id": "run"}),
    ],
)
def test_forwards_pass_arguments_and_plus_resolver_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    args: tuple[object, ...],
    values: dict[str, object],
) -> None:
    expected: dict[str, object] = {"format": MARKER}
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def forward(*given: object, **keywords: object) -> dict[str, object]:
        calls.append((given, keywords))
        return expected

    monkeypatch.setattr(core, operation, forward)
    result = getattr(control, operation)(*args, state_home=tmp_path, **values)
    assert result is expected  # Core's result is returned untransformed.
    keywords = {"state_home": tmp_path, **values}
    assert calls == [(args, {**keywords, "adapter_resolver": control.resolve_adapter})]


def test_command_instance_reads_equal_core_with_the_plus_resolver(
    single_flow: tuple[Path, Path, Path],
) -> None:
    instance, home = _instance(single_flow)
    legacy = start_run(instance, state_home=home)
    run = str(legacy["id"])
    invocation = legacy["invocations"][0]
    resolver = {"adapter_resolver": control.resolve_adapter}
    pairs = [
        (
            control.list_instances_page(state_home=home),
            core.list_instances_page(state_home=home, **resolver),
        ),
        (
            control.inspect_instance_page(instance, state_home=home),
            core.inspect_instance_page(instance, state_home=home, **resolver),
        ),
        (
            control.inspect_run_page(instance, run, state_home=home),
            core.inspect_run_page(instance, run, state_home=home, **resolver),
        ),
        (
            control.inspect_revision_graph(
                instance, run, str(legacy["revision_id"]), state_home=home
            ),
            core.inspect_revision_graph(
                instance, run, str(legacy["revision_id"]), state_home=home, **resolver
            ),
        ),
        (
            control.validate_revision_summary(instance, state_home=home, run_id=run),
            core.validate_revision_summary(
                instance, state_home=home, run_id=run, **resolver
            ),
        ),
    ]
    for ours, theirs in pairs:
        assert ours == theirs and ours["format"] == MARKER
    exact = control.inspect_run_page(
        instance,
        run,
        state_home=home,
        section="invocations",
        invocation_id=str(invocation["id"]),
    )
    (item,) = exact["items"]
    assert item["invocation_id"] == invocation["id"]
    assert item["producing_revision_id"] == invocation["revision_id"]
    assert (item["plane_id"], item["node_id"]) == ("main", "draft")
    assert exact["next_cursor"] is None


def test_native_adapter_instance_is_readable_only_with_the_plus_resolver(
    single_flow: tuple[Path, Path, Path],
) -> None:
    workflow, project, home, agent = native_flow(single_flow)
    instance = pi_instance(workflow, project, home, agent)  # created, never launched
    view = control.inspect_instance_page(instance, state_home=home)
    assert view["kind"] == "instance" and view["instance_id"] == instance
    with pytest.raises(RouterError) as legacy:
        core.inspect_instance(instance, state_home=home)
    with pytest.raises(RouterError) as bounded:
        core.inspect_instance_page(instance, state_home=home)
    assert bounded.value.code == legacy.value.code


@pytest.mark.parametrize(
    "marker,code",
    [
        (None, "core_contract_missing"),
        ("millrace-router-inspection/v0", "core_contract_mismatch"),
    ],
)
def test_missing_or_old_marker_refuses_inspection_only(
    single_flow: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    marker: str | None,
    code: str,
) -> None:
    instance, home = _instance(single_flow)
    reached: list[str] = []
    for name in FORWARDS:
        monkeypatch.setattr(
            core, name, lambda *args, _name=name, **kwargs: reached.append(_name)
        )
    monkeypatch.setattr(control, "INSPECTION_CONTRACT", marker)
    for name, args in _calls():
        with pytest.raises(CoreContractError) as error:
            getattr(control, name)(*args, state_home=home)
        assert error.value.code == code
        assert isinstance(error.value, ImportError)
    assert reached == []
    # Ordinary facade operations are unaffected.
    ordinary = control.inspect_instance(instance, state_home=home)
    assert ordinary["instance_id"] == instance


def test_missing_core_operation_refuses_even_with_the_marker(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, home = _instance(single_flow)
    monkeypatch.delattr(core, "inspect_run_page")
    with pytest.raises(CoreContractError) as error:
        control.inspect_run_page("a" * 32, "b" * 32, state_home=home)
    assert error.value.code == "core_contract_missing"


def test_facade_imports_and_operates_with_a_core_lacking_the_marker(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    instance, home = _instance(single_flow)
    monkeypatch.delattr(core, "INSPECTION_CONTRACT")
    name = "millrace_plus.router._facade_without_inspection"
    spec = importlib.util.spec_from_file_location(name, control.__file__)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)  # Import succeeds against the older Core.
    assert module.INSPECTION_CONTRACT is None
    assert module.inspect_instance(instance, state_home=home)["instance_id"] == instance
    with pytest.raises(CoreContractError) as error:
        module.list_instances_page(state_home=home)
    assert error.value.code == "core_contract_missing"
