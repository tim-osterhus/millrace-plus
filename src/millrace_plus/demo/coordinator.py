"""One synchronous owner for demo controls, capture, export and shutdown."""

from __future__ import annotations

import hashlib
import re
import select
import sys
import time
import uuid
import webbrowser
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypedDict
from urllib.parse import urlsplit, urlunsplit

from millrace_plus.subworkflow_records import SubworkflowError, public_request_digest
from millrace_plus.workflow_os_client import WorkflowOSClient, validate_call

from .contracts import (
    DemoError,
    canonical_bytes,
    load_document,
    validate_baseline,
    validate_report,
)
from .materialize import materialize
from .publication import producer_at, publish_document
from .render import build_report, standalone_html
from .retention import cleanup_root, create_root, export_results, write_private

if TYPE_CHECKING:
    from millrace_os.demo_service import DemoServiceHandle

__all__ = ["DemoOptions", "Coordinator", "run_demo"]


@dataclass(frozen=True)
class DemoOptions:
    output: Path
    no_open: bool = False
    keep_demo: bool = False
    no_web: bool = False
    native: dict[str, Any] | None = None
    native_consent: bool = False
    interactive: bool = False


class Coordinator:
    """The handle owns OS; this object owns public graph intent and file captures."""

    def __init__(self, options: DemoOptions, versions: dict[str, str]) -> None:
        self.options, self.versions = options, versions
        self.root: Path | None = None
        self.owner: dict[str, Any] = {}
        self.handle: DemoServiceHandle | None = None
        self.client: WorkflowOSClient | None = None
        self.instance: str | None = None
        self.run_id: str | None = None
        self.requests: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []
        self.accepted: list[dict[str, Any]] = []
        self.baseline: dict[str, Any] | None = None
        self.baseline_bytes: bytes | None = None
        self.report: dict[str, Any] | None = None
        self.session_bytes: bytes | None = None
        self.summary: dict[str, Any] = {}
        self.stop_source: str | None = None
        self.cancelled = False
        self.error: str | None = None
        self.admission_failed = False
        self.current: dict[str, Any] | None = None
        self.native = options.native
        self.digests: dict[str, str | None] = {
            "graph_sha256": None,
            "reference_sha256": None,
            "estimator_sha256": None,
        }
        self.started_at = _now()
        self.service_start_attempted = False
        self.service_ready_at: str | None = None
        self.graph_completed_at: str | None = None
        self.shutdown_observed_at: str | None = None
        self.node_observations: list[dict[str, Any]] = []

    @property
    def state_home(self) -> Path:
        assert self.root is not None
        return self.root / "router-state"

    def _record_requests(self) -> None:
        assert self.root is not None
        write_private(self.root / "requests.json", canonical_bytes(self.requests))

    def _submit(
        self,
        operation: str,
        payload: dict[str, Any],
        *,
        version: int | None,
        plane_id: str | None = None,
        deadline: float | None = None,
    ) -> dict[str, Any]:
        assert self.client is not None
        deadline = time.monotonic() + 30 if deadline is None else deadline
        request: dict[str, object] = {
            "format": "millrace-router-request/v2",
            "operation": operation,
            "instance_id": self.instance,
            "run_id": None
            if operation in {"create_instance", "start_run"}
            else self.run_id,
            "plane_id": plane_id,
            "request_id": uuid.uuid4().hex,
            "expected_state_version": version,
            "payload": payload,
        }
        # Fixed request/v2 construction; the existing public OS contract validates
        # the envelope before Core mutation. No private Core parser is imported.
        self.requests.append(request)
        self._record_requests()  # Durable exact identity precedes every possible POST.
        self.current = request
        try:
            observed = self.client.submit(request, deadline=deadline)
        except Exception:
            # A possible POST is never replaced/reposted. GET only, same identity.
            observed = self.client.observe(request, deadline=deadline)
        self._remember(observed)
        return observed

    def _remember(self, observed: dict[str, Any]) -> None:
        self.calls = [
            row for row in self.calls if row["request_id"] != observed["request_id"]
        ]
        self.calls.append(observed)
        if observed.get("instance_id") is not None:
            self.instance = str(observed["instance_id"])
        if observed.get("run_id") is not None:
            self.run_id = str(observed["run_id"])

    def _observe(self, *, deadline: float | None = None) -> dict[str, Any]:
        assert self.client is not None and self.current is not None
        observed = self.client.observe(
            self.current, deadline=deadline or time.monotonic() + 30
        )
        self._remember(observed)
        return observed

    def _joined(self, observed: dict[str, Any]) -> bool:
        return (
            observed.get("source") == "os_owned_call"
            and observed.get("holder_joined") is True
            and observed.get("phase") == "call_returned"
            and (observed.get("core_receipt") or {}).get("status")
            in {"applied", "refused"}
        )

    def _await_join(self, observed: dict[str, Any]) -> dict[str, Any]:
        while not self._joined(observed):
            self._check_stop()
            time.sleep(0.2)
            observed = self._observe()
        if (observed.get("core_receipt") or {}).get("status") == "refused":
            raise DemoError("control_refused")
        return observed

    def _summary(self) -> dict[str, Any]:
        from millrace_plus.router import control

        assert self.instance is not None and self.run_id is not None
        data = control.inspect_run_page(
            self.instance, self.run_id, state_home=self.state_home
        )["data"]
        if type(data) is not dict:
            raise DemoError("invalid_run_summary")
        self.summary = data
        return self.summary

    def _invocations(self) -> list[dict[str, Any]]:
        from millrace_plus.router import control

        assert self.instance is not None and self.run_id is not None
        page = control.inspect_run_page(
            self.instance,
            self.run_id,
            state_home=self.state_home,
            section="invocations",
        )
        # The selected acyclic graph has at most seven invocations. Refuse ambiguity.
        if page["next_cursor"] is not None:
            raise DemoError("ambiguous_artifact_producer")
        items = page["items"]
        if type(items) is not list:
            raise DemoError("invalid_invocation_page")
        rows: list[dict[str, Any]] = []
        for item in items:
            if type(item) is not dict:
                raise DemoError("invalid_invocation_page")
            rows.append(item)
        return rows

    def _check_stop(self) -> None:
        if self.handle is not None and self.handle.finish_requested:
            self.stop_source = "finish_request"
            raise _Stop
        if self.options.interactive:
            readable, _, _ = select.select([sys.stdin], [], [], 0)
            if readable and sys.stdin.readline() != "":
                self.stop_source = "terminal_enter"
                raise _Stop

    def _publish(
        self, value: dict[str, Any], output_id: str, identity: dict[str, Any]
    ) -> dict[str, Any]:
        assert self.instance is not None and self.run_id is not None
        accepted = publish_document(
            value,
            output_id=output_id,
            identity=identity,
            instance_id=self.instance,
            run_id=self.run_id,
            state_home=self.state_home,
        )
        self.accepted.insert(0, accepted)
        assert self.handle is not None
        self.handle.update_outputs(self.accepted)
        return accepted

    def _capture_gate(self) -> None:
        assert self.root is not None
        summary = self._summary()
        plane = summary.get("planes", {}).get("main", {})
        if (
            summary["status"] != "paused"
            or summary.get("run_gate") is not None
            or plane.get("status") != "paused"
            or plane.get("pause_reason") != "baseline_saved"
            or plane.get("resume_target") != ("native" if self.native else "skip")
        ):
            raise DemoError("unexpected_baseline_gate")
        identity = producer_at(
            summary, self._invocations(), node="baseline", action="pause"
        )
        self.baseline = validate_baseline(
            load_document(self.root / "workspace/baseline.json")
        )
        self.baseline_bytes = canonical_bytes(self.baseline)
        write_private(self.root / "captured-baseline.json", self.baseline_bytes)
        accepted = self._publish(self.baseline, "baseline", identity)
        session = {
            "format": "millrace-demo-session/v1",
            "instance_id": self.instance,
            "run_id": self.run_id,
            "plane_id": identity["plane_id"],
            "invocation_id": identity["invocation_id"],
            "producing_revision_id": identity["producing_revision_id"],
            "state_home": str(self.state_home),
            "baseline": accepted,
        }
        self.session_bytes = canonical_bytes(session)
        write_private(self.root / "demo-session.json", self.session_bytes)
        write_private(
            self.root / "capture-manifest.json",
            canonical_bytes(
                {
                    "baseline_sha256": hashlib.sha256(self.baseline_bytes).hexdigest(),
                    "session_sha256": hashlib.sha256(self.session_bytes).hexdigest(),
                    "baseline": accepted,
                    "identity": identity,
                }
            ),
        )
        self._check_stop()
        fresh = self._summary()
        if fresh != summary:
            raise DemoError("baseline_gate_changed")
        self._submit(
            "resume_plane",
            {},
            version=summary["state_version"],
            plane_id=identity["plane_id"],
        )

    def _capture_final(self) -> None:
        assert self.root is not None
        summary = self._summary()
        node = "unavailable" if self.baseline is None else "render"
        identity = producer_at(summary, self._invocations(), node=node, action="finish")
        if self.baseline is None:
            self.baseline = validate_baseline(
                load_document(self.root / "workspace/baseline.json")
            )
            self.baseline_bytes = canonical_bytes(self.baseline)
            self._publish(self.baseline, "baseline", identity)
        elif self.session_bytes != (
            self.root / "demo-session.json"
        ).read_bytes() or self.baseline_bytes != canonical_bytes(
            load_document(self.root / "workspace/baseline.json")
        ):
            raise DemoError("captured_input_changed")
        self.report = validate_report(
            load_document(self.root / "workspace/report.json")
        )
        if canonical_bytes(self.report["baseline"]) != self.baseline_bytes:
            raise DemoError("report_baseline_mismatch")
        if self.report["completion"] != "complete":
            raise DemoError("incomplete_final_report")
        self._publish(self.report, "report", identity)

    def _disclose_native_timeout(self) -> None:
        from millrace_plus.router import control

        assert self.instance is not None
        validation = control.validate_revision_summary(
            self.instance, state_home=self.state_home
        )["data"]
        if type(validation) is not dict or validation["valid"] is not True:
            raise DemoError("native_validation_failed")
        common = validation["resolved_nodes"]["main"]["native"]["common"]
        timeout = common["effective"]["timeout_seconds"]
        provenance = common["provenance"]["timeout_seconds"]
        print(
            f"Admitted native node timeout: {timeout} seconds; "
            f"source: {provenance['source']}; policy: {provenance['policy']}. "
            "Core's captured launch settings remain authoritative.",
            flush=True,
        )

    def _start(self) -> None:
        import secrets

        from millrace_os.demo_service import start_demo_service

        self.root, self.owner = create_root()
        workspace = self.root / "workspace"
        workflow = materialize(
            workspace,
            native=self.native,
            no_web=self.options.no_web,
            session_file=self.root / "demo-session.json",
        )
        self._capture_inputs(workflow)
        self.service_start_attempted = True
        self.handle = start_demo_service(self.root, owner_token=secrets.token_bytes(32))
        self.service_ready_at = _now()
        profile = {
            "format": "millrace-plus-workflow-os-profile/v1",
            "os_origin": self.handle.endpoint,
            "os_home": "demo",
            "os_credential_file": str(self.root / "os-config/credentials.json"),
        }
        profile_path = self.root / "os-config/control-profile.json"
        profile_raw = canonical_bytes(profile)
        write_private(profile_path, profile_raw)
        self.client = WorkflowOSClient(
            {
                "path": str(profile_path),
                "sha256": hashlib.sha256(profile_raw).hexdigest(),
            },
            "demo",
        )
        observed = self._submit(
            "create_instance",
            {
                "workflow_file": str(workflow),
                "project_root": str(workspace),
                "path": [],
                "env_allowlist": [],
                "host_paths": {},
            },
            version=0,
        )
        try:
            self._await_join(observed)
        except DemoError:
            if (
                self.native is None
                or (self.calls[-1].get("core_receipt") or {}).get("status") != "refused"
            ):
                raise
            # Known refused admission has no activated run. Preserve that candidate.
            workspace.rename(self.root / "admission-workspace")
            workspace.mkdir(mode=0o700)
            self.native = None
            self.admission_failed = True
            self.instance = None
            workflow = materialize(
                workspace,
                native=None,
                no_web=self.options.no_web,
                session_file=self.root / "demo-session.json",
            )
            self._capture_inputs(workflow)
            write_private(
                workspace / "native-status.json",
                canonical_bytes(
                    {
                        "status": "admission_failed",
                        "phase": None,
                        "reason": "control_refused",
                        "candidate": None,
                    }
                ),
            )
            observed = self._submit(
                "create_instance",
                {
                    "workflow_file": str(workflow),
                    "project_root": str(workspace),
                    "path": [],
                    "env_allowlist": [],
                    "host_paths": {},
                },
                version=0,
            )
            self._await_join(observed)
        assert self.instance is not None
        instance = self.client.read(self.instance, deadline=time.monotonic() + 30)
        version = instance["state_version"]
        if type(version) is not int:
            raise DemoError("invalid_instance_version")
        if self.native is not None:
            self._disclose_native_timeout()
        observed = self._submit("start_run", {"edit_mode": "locked"}, version=version)
        while self.run_id is None:
            if (observed.get("core_receipt") or {}).get("status") == "refused":
                raise DemoError("control_refused")
            self._check_stop()
            time.sleep(0.2)
            observed = self._observe()
        url = self.handle.bootstrap_url(instance_id=self.instance, run_id=self.run_id)
        split = urlsplit(url)
        print(
            "Demo: "
            + urlunsplit((split.scheme, split.netloc, split.path, split.query, "")),
            flush=True,
        )
        opened = False
        if not self.options.no_open:
            try:
                opened = bool(webbrowser.open(url))
            except Exception:
                pass
        if not opened:
            print("Local authentication (one use, 60 seconds): " + url, flush=True)
        if self.options.interactive:
            print(
                "Press Enter or use Finish to cancel while running "
                "or close the completed demo.",
                flush=True,
            )

    def execute(self) -> int:
        interrupted = False
        try:
            self._start()
            while True:
                self._check_stop()
                observed = self._observe()
                if (observed.get("core_receipt") or {}).get("status") == "refused":
                    raise DemoError("control_refused")
                summary = self._summary()
                if summary["status"] == "paused":
                    if not self._joined(observed):
                        self._await_join(observed)
                        continue  # Re-read the gate after the continuation joined.
                    if self.baseline is not None:
                        raise DemoError("graph_paused")
                    try:
                        self._capture_gate()
                    except Exception:
                        self._output_error("baseline_capture_failed")
                        raise
                elif summary["status"] == "succeeded":
                    self._await_join(observed)
                    try:
                        self._capture_final()
                    except Exception:
                        self._output_error("report_capture_failed")
                        raise
                    self.graph_completed_at = _now()
                    break
                elif summary["status"] in {"failed", "cancelled"}:
                    raise DemoError("graph_incomplete")
                time.sleep(0.2)
            while self.options.interactive:
                self._check_stop()
                time.sleep(0.2)
        except _Stop:
            self.cancelled = self.report is None
        except KeyboardInterrupt:
            interrupted = True
            self.stop_source = "sigint"
            self.cancelled = self.report is None
        except Exception as exc:
            self.error = str(exc) if isinstance(exc, DemoError) else type(exc).__name__
        try:
            status = self._finish()
        except KeyboardInterrupt:
            interrupted = True
            self.stop_source = "sigint"
            status = 1
            if self.handle is not None:
                self.handle.close()
            if self.root is not None:
                print("Demo state retained: " + str(self.root), flush=True)
        except Exception as exc:
            self.error = self.error or (
                str(exc) if isinstance(exc, DemoError) else type(exc).__name__
            )
            status = 1
            if self.root is not None and self.root.exists():
                print("Demo state retained: " + str(self.root), flush=True)
        return 130 if interrupted else status

    def _output_error(self, code: str) -> None:
        if self.handle is not None:
            try:
                self.handle.update_outputs(self.accepted, output_error=code)
            except Exception:
                pass  # A failed owner channel grants no new authority or output pin.

    def _finish(self) -> int:
        # A start whose response was lost may own writers despite an unknown run ID.
        stopped = self.run_id is None and not any(
            request["operation"] == "start_run" for request in self.requests
        )
        child_exited = False if self.service_start_attempted else self.handle is None
        shutdown: dict[str, Any] = {}
        joins_confirmed = False
        snapshot_saved = False
        cancel: dict[str, Any] | None = None
        deadline = time.monotonic() + 15
        try:
            if self.run_id is not None:
                summary = self._summary()
                if summary["status"] not in {"succeeded", "failed", "cancelled"}:
                    self.cancelled = self.cancelled or self.stop_source is not None
                    cancel = self._submit(
                        "cancel_run", {}, version=None, deadline=deadline
                    )
            while True:
                joins_confirmed = self._refresh_saved_joins(deadline=deadline)
                if cancel is not None:
                    cancel_id = cancel.get("request_id")
                    cancel = next(
                        (row for row in self.calls if row["request_id"] == cancel_id),
                        cancel,
                    )
                    if (
                        self._joined(cancel)
                        and (cancel.get("core_receipt") or {}).get("status")
                        != "applied"
                    ):
                        raise DemoError("cancel_refused")
                if self.run_id is not None:
                    summary = self._summary()
                    stopped = summary.get("quiescent") is True and not summary.get(
                        "inflight_invocations"
                    )
                if stopped and joins_confirmed:
                    if time.monotonic() >= deadline:
                        raise DemoError("cleanup_observation_expired")
                    break
                if time.monotonic() >= deadline:
                    raise DemoError(
                        "control_join_not_observed"
                        if not joins_confirmed
                        else "writer_closure_not_observed"
                    )
                time.sleep(min(0.2, max(0, deadline - time.monotonic())))
            if self.run_id is not None:
                if summary["status"] in {"succeeded", "failed", "cancelled"}:
                    self.graph_completed_at = self.graph_completed_at or _now()
        except Exception as exc:
            self.error = self.error or (
                str(exc) if isinstance(exc, DemoError) else type(exc).__name__
            )
        finally:
            try:
                if not joins_confirmed:
                    joins_confirmed = self._refresh_saved_joins(deadline=deadline)
                self._write_join_snapshot()
                snapshot_saved = True
            except Exception as exc:
                self.error = self.error or (
                    str(exc) if isinstance(exc, DemoError) else type(exc).__name__
                )
            if self.handle is not None:
                try:
                    self.handle.finish(cancel=self.cancelled or self.error is not None)
                except Exception as exc:
                    self.error = self.error or type(exc).__name__
                finally:
                    try:
                        child_exited = self.handle.wait()
                        shutdown = self.handle.shutdown_result
                        self.shutdown_observed_at = _now()
                    except Exception as exc:
                        self.error = self.error or type(exc).__name__
                    finally:
                        self.handle.close()
        if self.root is None:
            return 1
        if self.baseline_bytes is not None and stopped:
            # Restore only after the public writer proof, never while native may live.
            write_private(self.root / "workspace/baseline.json", self.baseline_bytes)
        if self.report is None and self.baseline is not None:
            native_status = (
                "admission_failed"
                if self.admission_failed
                else "skipped"
                if self.native is None
                else "writer_unconfirmed"
                if not stopped
                else "cancelled"
                if self.cancelled
                else "unknown_outcome"
            )
            self.report = build_report(
                self.owner["demo_id"],
                self.baseline,
                native={
                    "status": native_status,
                    "phase": None,
                    "reason": None,
                    "candidate": None,
                },
                completion="cancelled" if self.cancelled else "incomplete",
            )
        confirmed = (
            stopped
            and joins_confirmed
            and snapshot_saved
            and self.error is None
            and child_exited
            and shutdown.get("workers_drained") is True
            and shutdown.get("service_stopped") is True
        )
        files_to_export: dict[str, bytes] = {}
        if self.baseline is not None:
            assert self.baseline_bytes is not None
            files_to_export["baseline.json"] = self.baseline_bytes
            files_to_export["baseline.html"] = standalone_html(self.baseline).encode()
        if self.report is not None:
            files_to_export["report.json"] = canonical_bytes(
                validate_report(self.report)
            )
            files_to_export["report.html"] = standalone_html(self.report).encode()
        if self.run_id is not None:
            try:
                self.node_observations = self._invocations()
            except Exception:
                # Missing public observations stay missing; no log/store inference.
                self.node_observations = []
        execution = self._execution_record(
            files_to_export,
            stopped=stopped,
            child_exited=child_exited,
            shutdown=shutdown,
            cleanup="pending",
            exported_at=None,
        )
        files_to_export["execution.json"] = canonical_bytes(execution)
        exported = False
        try:
            for name, raw in files_to_export.items():
                write_private(self.root / "exports" / name, raw)
            result, hashes = export_results(self.options.output, files_to_export)
            exported = all(
                hashlib.sha256((result / name).read_bytes()).hexdigest() == sha
                for name, sha in hashes.items()
            )
            for name in ("report.html", "report.json", "execution.json"):
                if name in files_to_export:
                    print(str(result / name), flush=True)
        except (OSError, ValueError):
            self.error = self.error or "export_failed"
        removed = False
        if (
            not self.options.keep_demo
            and joins_confirmed
            and snapshot_saved
            and self.error is None
        ):
            removed = cleanup_root(
                self.root,
                self.owner,
                writers_stopped=stopped,
                child_exited=child_exited,
                workers_drained=shutdown.get("workers_drained") is True,
                service_stopped=shutdown.get("service_stopped") is True,
                exports_verified=exported,
            )
        if exported:
            execution["retention"]["cleanup"] = "removed" if removed else "retained"
            execution["retention"]["reason"] = (
                None
                if removed
                else "keep_demo"
                if self.options.keep_demo
                else "closure_unconfirmed"
                if not confirmed
                else "ownership_unconfirmed"
            )
            execution["timestamps"]["exported_at"] = _now()
            execution["timestamps"]["cleanup_observed_at"] = _now()
            try:
                _validate_execution(execution)
                encoded = canonical_bytes(execution)
                write_private(result / "execution.json", encoded)
                if (result / "execution.json").read_bytes() != encoded:
                    raise OSError("execution_annotation_verification_failed")
            except (OSError, ValueError):
                self.error = self.error or "execution_annotation_failed"
                print(
                    "Execution annotation failed; saved exports: " + str(result),
                    flush=True,
                )
        if not removed:
            print("Demo state retained: " + str(self.root), flush=True)
        if not confirmed or not exported or self.error:
            return 1
        if self.cancelled:
            return 130
        return (
            0
            if self.report is not None and self.report["completion"] == "complete"
            else 1
        )

    def _capture_inputs(self, workflow: Path) -> None:
        self.digests = {
            "graph_sha256": _sha(workflow.read_bytes()),
            "reference_sha256": _sha(
                (workflow.parent / "references/shortlist.json").read_bytes()
            ),
            "estimator_sha256": _sha(
                files("millrace_plus.demo").joinpath("estimate.py").read_bytes()
            ),
        }

    def _execution_record(
        self,
        artifacts: dict[str, bytes],
        *,
        stopped: bool,
        child_exited: bool,
        shutdown: dict[str, Any],
        cleanup: str,
        exported_at: str | None,
    ) -> ExecutionRecord:
        observed = {row["request_id"]: row for row in self.calls}
        requests = []
        for request in self.requests:
            call = observed.get(request["request_id"], {})
            receipt = call.get("core_receipt") or {}
            requests.append(
                {
                    "request_id": request["request_id"],
                    "operation": request["operation"],
                    "plane_id": request.get("plane_id"),
                    "expected_state_version": request["expected_state_version"],
                    "request_digest": call.get("request_digest"),
                    "status": _safe_code(receipt.get("status")),
                    "code": _safe_code(receipt.get("code")),
                    "holder_joined": call.get("holder_joined"),
                }
            )
        nodes = []
        native = self.report["native"] if self.report is not None else {}
        for row in self.node_observations:
            completion = row.get("completion") or {}
            nodes.append(
                {
                    "node_id": row["node_id"],
                    "plane_id": row["plane_id"],
                    "invocation_id": row["invocation_id"],
                    "producing_revision_id": row["producing_revision_id"],
                    "outcome": _safe_code(completion.get("outcome")),
                    "phase": _safe_code(native.get("phase"))
                    if row["node_id"] == "native"
                    else None,
                    "writer": _safe_code((row.get("writer") or {}).get("effective")),
                    "transport": _safe_code(completion.get("transport")),
                    "classification": _safe_code(completion.get("classification")),
                }
            )
        record: ExecutionRecord = {
            "format": "millrace-demo-execution/v1",
            "demo_id": self.owner["demo_id"],
            "combination": "local_candidate",
            "versions": {
                name: self.versions[name]
                for name in ("millrace-ai", "millrace-plus", "millrace-os")
                if name in self.versions
            },
            "instance_id": self.instance,
            "run_id": self.run_id,
            "completion": "cancelled"
            if self.cancelled
            else "complete"
            if self.report is not None
            and self.report["completion"] == "complete"
            and not self.error
            else "incomplete",
            "stop_source": self.stop_source,
            "error": _safe_code(self.error),
            "digests": dict(self.digests),
            "requests": requests,
            "nodes": nodes,
            "native": {
                "adapter": _safe_code(
                    (self.options.native or {}).get("binding", {}).get("adapter")
                ),
                "choice": "selected"
                if self.options.native is not None
                else "not_selected",
                "consent": self.options.native_consent,
                "no_web": self.options.no_web,
                "web_requested": self.options.native is not None
                and not self.options.no_web,
                "admission_failed": self.admission_failed,
                "status": _safe_code(native.get("status")),
                "phase": _safe_code(native.get("phase")),
            },
            "artifacts": {name: _sha(raw) for name, raw in artifacts.items()},
            "accepted_outputs": self.accepted,
            "writers_stopped": stopped,
            "child_exited": child_exited,
            "shutdown": {
                key: shutdown.get(key)
                for key in (
                    "workers_drained",
                    "service_stopped",
                    "cancel_requested",
                    "returncode",
                )
            },
            "service_start_observed": self.service_ready_at is not None,
            "retention": {
                "keep_demo": self.options.keep_demo,
                "cleanup": cleanup,
                "reason": None,
            },
            "timestamps": {
                "started_at": self.started_at,
                "service_ready_at": self.service_ready_at,
                "graph_terminal_observed_at": self.graph_completed_at,
                "shutdown_observed_at": self.shutdown_observed_at,
                "exported_at": exported_at,
                "cleanup_observed_at": None,
            },
        }
        _validate_execution(record)
        return record

    def _refresh_saved_joins(self, *, deadline: float) -> bool:
        """Refresh every pending original identity without POST or a new budget."""
        saved = {row["request_id"]: row for row in self.calls}
        joined = True
        for request in self.requests:
            observed = saved.get(request["request_id"])
            if observed is not None:
                try:
                    validate_call(observed, "demo", request)
                except Exception as exc:
                    self.calls = [
                        row
                        for row in self.calls
                        if row["request_id"] != request["request_id"]
                    ]
                    observed = None
                    self.error = self.error or (
                        str(exc)
                        if isinstance(exc, SubworkflowError)
                        else type(exc).__name__
                    )
            try:
                if observed is None or not self._joined(observed):
                    if time.monotonic() >= deadline:
                        raise DemoError("control_join_not_observed")
                    assert self.client is not None
                    fresh = self.client.observe(request, deadline=deadline)
                    validate_call(fresh, "demo", request)
                    if time.monotonic() >= deadline:
                        raise DemoError("control_join_not_observed")
                    observed = fresh
                    self._remember(observed)
                if not self._joined(observed):
                    joined = False
            except Exception as exc:
                # Invalid fresh rows cannot replace validated saved facts.
                self.error = self.error or (
                    str(exc)
                    if isinstance(exc, (DemoError, SubworkflowError))
                    else type(exc).__name__
                )
                joined = False
        return joined

    def _write_join_snapshot(self) -> None:
        assert self.root is not None
        observed = {row["request_id"]: row for row in self.calls}
        rows = []
        for request in self.requests:
            call = observed.get(request["request_id"])
            safe_call = None
            if call is not None:
                validate_call(call, "demo", request)
                receipt = call["core_receipt"]
                safe_call = {
                    key: call[key]
                    for key in (
                        "instance_id",
                        "run_id",
                        "source",
                        "phase",
                        "holder_joined",
                    )
                }
                safe_call["receipt"] = (
                    {
                        key: _safe_code(receipt.get(key))
                        for key in (
                            "request_id",
                            "request_digest",
                            "operation",
                            "instance_id",
                            "run_id",
                            "plane_id",
                            "status",
                            "code",
                        )
                    }
                    if receipt is not None
                    else None
                )
            rows.append(
                {
                    "request_id": request["request_id"],
                    "request_digest": public_request_digest(request),
                    "operation": request["operation"],
                    "target": {
                        key: request[key]
                        for key in ("instance_id", "run_id", "plane_id")
                    },
                    "observation": safe_call,
                }
            )
        write_private(
            self.root / "join-observations.json", canonical_bytes({"requests": rows})
        )


class ExecutionRecord(TypedDict):
    format: Literal["millrace-demo-execution/v1"]
    demo_id: str
    combination: Literal["local_candidate"]
    versions: dict[str, str]
    instance_id: str | None
    run_id: str | None
    completion: str
    stop_source: str | None
    error: str | None
    digests: dict[str, str | None]
    requests: list[dict[str, Any]]
    nodes: list[dict[str, Any]]
    native: dict[str, Any]
    artifacts: dict[str, str]
    accepted_outputs: list[dict[str, Any]]
    writers_stopped: bool
    child_exited: bool
    shutdown: dict[str, Any]
    service_start_observed: bool
    retention: dict[str, Any]
    timestamps: dict[str, str | None]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _safe_code(value: object) -> str | None:
    return (
        value
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value)
        else None
    )


def _validate_execution(value: Mapping[str, Any]) -> None:
    """Validate the allowlisted execution/v1 record and its selected byte bound."""
    nested = {
        "digests": {"graph_sha256", "reference_sha256", "estimator_sha256"},
        "native": {
            "adapter",
            "choice",
            "consent",
            "web_requested",
            "no_web",
            "admission_failed",
            "status",
            "phase",
        },
        "shutdown": {
            "workers_drained",
            "service_stopped",
            "cancel_requested",
            "returncode",
        },
        "retention": {"keep_demo", "cleanup", "reason"},
        "timestamps": {
            "started_at",
            "service_ready_at",
            "graph_terminal_observed_at",
            "shutdown_observed_at",
            "exported_at",
            "cleanup_observed_at",
        },
    }
    if (
        value.keys() != ExecutionRecord.__annotations__.keys()
        or any(
            type(value[key]) is not dict or value[key].keys() != keys
            for key, keys in nested.items()
        )
        or any(
            row.keys()
            != {
                "request_id",
                "operation",
                "plane_id",
                "expected_state_version",
                "request_digest",
                "status",
                "code",
                "holder_joined",
            }
            for row in value["requests"]
        )
        or any(
            row.keys()
            != {
                "node_id",
                "plane_id",
                "invocation_id",
                "producing_revision_id",
                "outcome",
                "phase",
                "writer",
                "transport",
                "classification",
            }
            for row in value["nodes"]
        )
        or value["format"] != "millrace-demo-execution/v1"
        or value["combination"] != "local_candidate"
        or value["completion"] not in {"complete", "incomplete", "cancelled"}
        or value["stop_source"]
        not in {None, "finish_request", "terminal_enter", "sigint"}
        or value["retention"]["cleanup"] not in {"pending", "removed", "retained"}
        or any(
            item is not None and re.fullmatch(r"[0-9a-f]{64}", item) is None
            for item in [*value["digests"].values(), *value["artifacts"].values()]
        )
        or len(canonical_bytes(value)) > 64 * 1024
    ):
        raise DemoError("invalid_execution_record")


class _Stop(Exception):
    pass


def run_demo(options: DemoOptions, versions: dict[str, str]) -> int:
    return Coordinator(options, versions).execute()
