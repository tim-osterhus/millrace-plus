"""Plus request facade: the same public Core controls with the Pi resolver injected.

Each function forwards to the identically named `millrace.router.control`
operation and passes `adapter_resolver=resolve_adapter` by keyword. Core keeps
every lock, expected version, request ID, receipt, drain guard and writer
hold; this module owns no state, scheduler or process-global resolver.
Callers cannot substitute another resolver here. Core resolves its builtin
`command` adapter itself and never consults this resolver for it.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import cast

from millrace_plus import catalog as _catalog

from . import require_core

require_core()

from millrace.router import control as _core  # noqa: E402 - after the handshake
from millrace.router.execution import RouterError  # noqa: E402

from .resolver import resolve_adapter  # noqa: E402

__all__ = [
    "resolve_adapter",
    "create_instance",
    "list_instances",
    "inspect_instance",
    "update_instance_config",
    "validate_revision",
    "submit_revision",
    "start_run",
    "inspect_run",
    "recover_run",
    "pause_run",
    "pause_plane",
    "resume_plane",
    "cancel_plane",
    "resume_run",
    "accept_revision",
    "redirect_plane",
    "retry_invocation",
    "cancel_run",
    "reconcile_invocation",
    "release_project_hold",
    "execute_request",
    "load_instance",
]

_OMITTED = object()


_DISPATCH = frozenset(
    {
        "start_run",
        "recover_run",
        "resume_run",
        "resume_plane",
        "retry_invocation",
        "redirect_plane",
        "accept_revision",
        "submit_revision",
        "update_instance_config",
    }
)
_SAFETY = frozenset(
    {"pause_run", "cancel_run", "pause_plane", "cancel_plane", "reconcile_invocation"}
)


def _guard(
    operation: str,
    instance_id: str,
    state_home: Path,
    run_id: str | None,
    catalog_home: Path | None,
) -> None:
    try:
        view = _core.inspect_instance(
            instance_id, state_home=state_home, adapter_resolver=resolve_adapter
        )
    except RouterError:
        return  # Core retains ordinary lookup/error semantics.
    try:
        _catalog.check_ownership(
            Path(str(view["workflow_file"])),
            Path(str(view["project_root"])),
            catalog_home=catalog_home,
        )
    except _catalog.CatalogError:
        if operation not in _SAFETY:
            raise
        runs = [
            row
            for row in cast(list[dict[str, object]], view["runs"])
            if row["status"] not in {"succeeded", "failed", "cancelled"}
        ]
        if len(runs) != 1 or runs[0]["id"] != run_id:
            raise _catalog.CatalogError(
                "catalog_ownership",
                "Unsafe instance stop must target its sole nonterminal run",
            ) from None
        if operation == "reconcile_invocation":
            try:
                run = _core.inspect_run(
                    instance_id,
                    str(run_id),
                    state_home=state_home,
                    adapter_resolver=resolve_adapter,
                )
            except RouterError as exc:
                raise _catalog.CatalogError(
                    "catalog_ownership",
                    "Cannot verify paused unsafe-instance reconciliation target",
                ) from exc
            if runs[0]["status"] != "paused" or run["status"] != "paused":
                raise _catalog.CatalogError(
                    "catalog_ownership",
                    "Unsafe-instance reconciliation requires the matching paused run",
                ) from None


def _guard_request(
    request: dict[str, object], state_home: Path, catalog_home: Path | None
) -> None:
    operation = request.get("operation")
    payload = request.get("payload")
    if operation == "create_instance" and isinstance(payload, dict):
        workflow, project = payload.get("workflow_file"), payload.get("project_root")
        if isinstance(workflow, str) and isinstance(project, str):
            _catalog.check_ownership(
                Path(workflow), Path(project), catalog_home=catalog_home
            )
    elif (
        isinstance(operation, str)
        and operation in _DISPATCH | _SAFETY
        and isinstance(request.get("instance_id"), str)
    ):
        _guard(
            str(operation),
            str(request["instance_id"]),
            state_home,
            cast(str, request["run_id"])
            if isinstance(request.get("run_id"), str)
            else None,
            catalog_home,
        )


def create_instance(
    workflow_file: Path,
    project_root: Path,
    *,
    state_home: Path,
    request_id: str,
    expected_state_version: int,
    path: tuple[str, ...] | None = None,
    env_allowlist: tuple[str, ...] = (),
    host_paths: dict[str, str] | None = None,
    runtime_defaults: object = _OMITTED,
    operator_policy: object = _OMITTED,
    catalog_home: Path | None = None,
) -> dict[str, object]:
    _catalog.check_ownership(workflow_file, project_root, catalog_home=catalog_home)
    layers = {
        key: value
        for key, value in (
            ("runtime_defaults", runtime_defaults),
            ("operator_policy", operator_policy),
        )
        if value is not _OMITTED
    }
    return _core.create_instance(
        workflow_file,
        project_root,
        state_home=state_home,
        request_id=request_id,
        expected_state_version=expected_state_version,
        path=path,
        env_allowlist=env_allowlist,
        host_paths=host_paths,
        adapter_resolver=resolve_adapter,
        **layers,
    )


def list_instances(*, state_home: Path) -> list[dict[str, object]]:
    return _core.list_instances(state_home=state_home, adapter_resolver=resolve_adapter)


def inspect_instance(instance_id: str, *, state_home: Path) -> dict[str, object]:
    return _core.inspect_instance(
        instance_id, state_home=state_home, adapter_resolver=resolve_adapter
    )


def update_instance_config(
    instance_id: str,
    *,
    settings: dict[str, object],
    state_home: Path,
    request_id: str,
    expected_state_version: int,
    catalog_home: Path | None = None,
) -> dict[str, object]:
    _guard("update_instance_config", instance_id, state_home, None, catalog_home)
    return _core.update_instance_config(
        instance_id,
        settings=settings,
        state_home=state_home,
        request_id=request_id,
        expected_state_version=expected_state_version,
        adapter_resolver=resolve_adapter,
    )


def validate_revision(
    instance_id: str,
    *,
    state_home: Path,
    workflow_file: Path | None = None,
    run_id: str | None = None,
) -> dict[str, object]:
    return _core.validate_revision(
        instance_id,
        state_home=state_home,
        workflow_file=workflow_file,
        run_id=run_id,
        adapter_resolver=resolve_adapter,
    )


def submit_revision(
    instance_id: str,
    run_id: str,
    *,
    state_home: Path,
    request_id: str,
    expected_state_version: int,
    catalog_home: Path | None = None,
) -> dict[str, object]:
    _guard("submit_revision", instance_id, state_home, run_id, catalog_home)
    return _core.submit_revision(
        instance_id,
        run_id,
        state_home=state_home,
        request_id=request_id,
        expected_state_version=expected_state_version,
        adapter_resolver=resolve_adapter,
    )


def start_run(
    instance_id: str,
    *,
    state_home: Path,
    request_id: str,
    expected_state_version: int,
    edit_mode: str = "locked",
    on_started: Callable[[dict[str, object]], None] | None = None,
    catalog_home: Path | None = None,
) -> dict[str, object]:
    _guard("start_run", instance_id, state_home, None, catalog_home)
    return _core.start_run(
        instance_id,
        state_home=state_home,
        request_id=request_id,
        expected_state_version=expected_state_version,
        edit_mode=edit_mode,
        on_started=on_started,
        adapter_resolver=resolve_adapter,
    )


def inspect_run(
    instance_id: str, run_id: str, *, state_home: Path
) -> dict[str, object]:
    return _core.inspect_run(
        instance_id, run_id, state_home=state_home, adapter_resolver=resolve_adapter
    )


def recover_run(
    instance_id: str,
    run_id: str,
    *,
    state_home: Path,
    request_id: str,
    expected_state_version: int,
    catalog_home: Path | None = None,
) -> dict[str, object]:
    _guard("recover_run", instance_id, state_home, run_id, catalog_home)
    return _core.recover_run(
        instance_id,
        run_id,
        state_home=state_home,
        request_id=request_id,
        expected_state_version=expected_state_version,
        adapter_resolver=resolve_adapter,
    )


def pause_run(
    instance_id: str,
    run_id: str,
    *,
    state_home: Path,
    request_id: str,
    catalog_home: Path | None = None,
) -> dict[str, object]:
    _guard("pause_run", instance_id, state_home, run_id, catalog_home)
    return _core.pause_run(
        instance_id,
        run_id,
        state_home=state_home,
        request_id=request_id,
        adapter_resolver=resolve_adapter,
    )


def resume_run(
    instance_id: str,
    run_id: str,
    *,
    state_home: Path,
    request_id: str,
    expected_state_version: int,
    catalog_home: Path | None = None,
) -> dict[str, object]:
    _guard("resume_run", instance_id, state_home, run_id, catalog_home)
    return _core.resume_run(
        instance_id,
        run_id,
        state_home=state_home,
        request_id=request_id,
        expected_state_version=expected_state_version,
        adapter_resolver=resolve_adapter,
    )


def accept_revision(
    instance_id: str,
    run_id: str,
    *,
    candidate_id: str,
    state_home: Path,
    request_id: str,
    expected_state_version: int,
    catalog_home: Path | None = None,
) -> dict[str, object]:
    _guard("accept_revision", instance_id, state_home, run_id, catalog_home)
    return _core.accept_revision(
        instance_id,
        run_id,
        candidate_id=candidate_id,
        state_home=state_home,
        request_id=request_id,
        expected_state_version=expected_state_version,
        adapter_resolver=resolve_adapter,
    )


def pause_plane(
    instance_id: str,
    run_id: str,
    *,
    plane_id: str,
    state_home: Path,
    request_id: str,
    catalog_home: Path | None = None,
) -> dict[str, object]:
    _guard("pause_plane", instance_id, state_home, run_id, catalog_home)
    return _core.pause_plane(
        instance_id,
        run_id,
        plane_id=plane_id,
        state_home=state_home,
        request_id=request_id,
        adapter_resolver=resolve_adapter,
    )


def resume_plane(
    instance_id: str,
    run_id: str,
    *,
    plane_id: str,
    state_home: Path,
    request_id: str,
    expected_state_version: int,
    catalog_home: Path | None = None,
) -> dict[str, object]:
    _guard("resume_plane", instance_id, state_home, run_id, catalog_home)
    return _core.resume_plane(
        instance_id,
        run_id,
        plane_id=plane_id,
        state_home=state_home,
        request_id=request_id,
        expected_state_version=expected_state_version,
        adapter_resolver=resolve_adapter,
    )


def cancel_plane(
    instance_id: str,
    run_id: str,
    *,
    plane_id: str,
    state_home: Path,
    request_id: str,
    catalog_home: Path | None = None,
) -> dict[str, object]:
    _guard("cancel_plane", instance_id, state_home, run_id, catalog_home)
    return _core.cancel_plane(
        instance_id,
        run_id,
        plane_id=plane_id,
        state_home=state_home,
        request_id=request_id,
        adapter_resolver=resolve_adapter,
    )


def redirect_plane(
    instance_id: str,
    run_id: str,
    *,
    plane_id: str,
    node_id: str,
    reason: str,
    state_home: Path,
    request_id: str,
    expected_state_version: int,
    catalog_home: Path | None = None,
) -> dict[str, object]:
    _guard("redirect_plane", instance_id, state_home, run_id, catalog_home)
    return _core.redirect_plane(
        instance_id,
        run_id,
        plane_id=plane_id,
        node_id=node_id,
        reason=reason,
        state_home=state_home,
        request_id=request_id,
        expected_state_version=expected_state_version,
        adapter_resolver=resolve_adapter,
    )


def retry_invocation(
    instance_id: str,
    run_id: str,
    *,
    plane_id: str,
    invocation_id: str,
    reason: str,
    state_home: Path,
    request_id: str,
    expected_state_version: int,
    catalog_home: Path | None = None,
) -> dict[str, object]:
    _guard("retry_invocation", instance_id, state_home, run_id, catalog_home)
    return _core.retry_invocation(
        instance_id,
        run_id,
        plane_id=plane_id,
        invocation_id=invocation_id,
        reason=reason,
        state_home=state_home,
        request_id=request_id,
        expected_state_version=expected_state_version,
        adapter_resolver=resolve_adapter,
    )


def cancel_run(
    instance_id: str,
    run_id: str,
    *,
    state_home: Path,
    request_id: str,
    catalog_home: Path | None = None,
) -> dict[str, object]:
    _guard("cancel_run", instance_id, state_home, run_id, catalog_home)
    return _core.cancel_run(
        instance_id,
        run_id,
        state_home=state_home,
        request_id=request_id,
        adapter_resolver=resolve_adapter,
    )


def reconcile_invocation(
    instance_id: str,
    run_id: str,
    *,
    plane_id: str,
    invocation_id: str,
    reason: str,
    stopped: bool,
    state_home: Path,
    request_id: str,
    expected_state_version: int,
    catalog_home: Path | None = None,
) -> dict[str, object]:
    _guard("reconcile_invocation", instance_id, state_home, run_id, catalog_home)
    return _core.reconcile_invocation(
        instance_id,
        run_id,
        plane_id=plane_id,
        invocation_id=invocation_id,
        reason=reason,
        stopped=stopped,
        state_home=state_home,
        request_id=request_id,
        expected_state_version=expected_state_version,
        adapter_resolver=resolve_adapter,
    )


def release_project_hold(
    *,
    project_root: Path,
    hold_id: str,
    owner_instance_id: str,
    owner_run_id: str,
    reason: str,
    stopped: bool,
    state_home: Path,
    request_id: str,
) -> dict[str, object]:
    return _core.release_project_hold(
        project_root=project_root,
        hold_id=hold_id,
        owner_instance_id=owner_instance_id,
        owner_run_id=owner_run_id,
        reason=reason,
        stopped=stopped,
        state_home=state_home,
        request_id=request_id,
        adapter_resolver=resolve_adapter,
    )


def execute_request(
    request: dict[str, object],
    *,
    state_home: Path,
    on_started: Callable[[dict[str, object]], None] | None = None,
    catalog_home: Path | None = None,
) -> dict[str, object]:
    _guard_request(request, state_home, catalog_home)
    return _core.execute_request(
        request,
        state_home=state_home,
        on_started=on_started,
        adapter_resolver=resolve_adapter,
    )


def load_instance(instance_id: str, state_home: Path) -> object:
    """Load one instance configuration through Core with the Plus resolver."""
    return _core.load_instance(
        instance_id, state_home, adapter_resolver=resolve_adapter
    )
