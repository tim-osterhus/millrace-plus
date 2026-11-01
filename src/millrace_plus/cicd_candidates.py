"""Local CI candidate operations and their exact public completion guards.

One target lease spans base preparation, repair/import, accepted check products,
sealing and reverse restoration. Operations retain intent before mutation; a
following declared guard accepts public producing completion and actual bytes.
Actor binding reads only public Core facts and never takes the active caller's
controller lock. An unpublished PID may be reobserved only to that invocation's
existing deadline.
"""

from __future__ import annotations

import hashlib
import math
import os
import stat
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, cast

from .cicd_records import CicdError
from .cicd_source import (
    baseline_links,
    filesystem_identity,
    source_identity,
    write_exact,
)
from .cicd_v2_records import (
    BEFORE,
    DELTA,
    TREE,
    tree_digest,
    validate_before,
    validate_delta,
    validate_tree,
)
from .local_intake import IntakeError, canonical

if TYPE_CHECKING:
    from .cicd_v2_runtime import V2Home

__all__ = [
    "CONTEXT_FIELDS",
    "match_invocation",
    "bind_invocation",
    "operate_candidate",
    "project_candidate_role",
    "prepare_candidate_review",
    "accept_materialization_operation",
    "accept_materialization",
    "accept_candidate_role",
    "accept_candidate_review",
    "retain_payloads",
    "tree_manifest",
    "tree_delta",
    "apply_job_delta",
    "apply_delta",
    "target_record",
    "before_manifest",
]

# Exact public-fact InvocationContext fields (spec 7.5).
CONTEXT_FIELDS = (
    "instance_id",
    "state_home",
    "run_id",
    "plane_id",
    "node",
    "attempt",
    "invocation_id",
    "revision_id",
    "controls_root",
    "pid",
    "cwd",
    "argv",
    "deadline",
    "public_run_ref",
)


def match_invocation(
    view: Mapping[str, object],
    *,
    instance_id: str,
    state_home: Path,
    controls_root: Path,
    expected_plane: str,
    expected_node: str,
    script: Path,
    pid: int,
    cwd: Path,
) -> dict[str, object]:
    """Validate this command against exactly one active public invocation.

    The plane's active invocation must be the single active row for that plane.
    It must name the expected node, run under the caller's PID and cwd, and
    launch the exact captured script. A missing or nonfinite deadline,
    mismatch or wrong plane refuses. `public_run_ref` is the hash of the
    observed view, retained as evidence, not as store access.
    """
    planes = cast(dict[str, dict[str, object]], view.get("planes") or {})
    plane = planes.get(expected_plane)
    if not isinstance(plane, dict) or not isinstance(
        plane.get("active_invocation"), str
    ):
        raise CicdError("invocation_not_active")
    active = plane["active_invocation"]
    rows = [
        row
        for row in cast(list[dict[str, object]], view.get("invocations") or [])
        if row.get("plane_id") == expected_plane and row.get("status") == "active"
    ]
    if len(rows) != 1 or rows[0].get("id") != active:
        raise CicdError("invocation_ambiguous")
    row = rows[0]
    if row.get("node") != expected_node:
        raise CicdError("invocation_node_mismatch")
    if row.get("pid") is None:
        raise CicdError("pid_unpublished")
    if type(row.get("pid")) is not int or row["pid"] != pid:
        raise CicdError("invocation_pid_mismatch")
    if row.get("cwd") != str(cwd):
        raise CicdError("invocation_cwd_mismatch")
    argv = row.get("argv")
    if not isinstance(argv, list) or str(script) not in argv:
        raise CicdError("invocation_argv_mismatch")
    deadline = row.get("deadline")
    if (
        isinstance(deadline, bool)
        or not isinstance(deadline, int | float)
        or not math.isfinite(deadline)
    ):
        raise CicdError("invocation_deadline_missing")
    attempt = row.get("attempt")
    if type(attempt) is not int or attempt < 1:
        raise CicdError("invocation_ambiguous")
    return {
        "instance_id": instance_id,
        "state_home": str(state_home),
        "run_id": str(view.get("id")),
        "plane_id": expected_plane,
        "node": expected_node,
        "attempt": attempt,
        "invocation_id": active,
        "revision_id": row.get("revision_id"),
        "controls_root": str(controls_root),
        "pid": pid,
        "cwd": str(cwd),
        "argv": list(cast(list[str], argv)),
        "deadline": deadline,
        "public_run_ref": _observation(view),
    }


def _observation(view: Mapping[str, object]) -> str:
    try:
        return hashlib.sha256(canonical(dict(view))).hexdigest()
    except IntakeError as exc:
        raise CicdError("invalid_public_view") from exc


def retain_payloads(
    target: Path, view: Mapping[str, object], bundle: Path
) -> dict[str, dict[str, object]]:
    """Copy each source file's exact bytes into the bundle's content store.

    The bundle lives outside both mutable workareas. Payloads are immutable,
    content-addressed (`payloads/<sha256>`) and refer to the bundle root. A
    byte that differs from the snapshot refuses `source_changed`; links are
    never followed.
    """
    refs: dict[str, dict[str, object]] = {}
    source = cast(dict[str, dict[str, object]], view["source"])
    for path in sorted(source):
        descriptor = os.open(target / path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                raw = stream.read()
        finally:
            os.close(descriptor)
        content = hashlib.sha256(raw).hexdigest()
        if content != source[path]["sha256"]:
            raise CicdError("source_changed")
        write_exact(bundle / "payloads" / content, raw, "payload_conflict")
        refs[path] = {"path": f"payloads/{content}", "sha256": content}
    return refs


def tree_manifest(
    view: Mapping[str, object],
    *,
    repository: str,
    anchor_head: str,
    policy_ref: Mapping[str, object],
    payload_refs: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    """The `millrace-plus-cicd-tree/v1` manifest of one classified snapshot.

    Gitlinks and special source entries hold: there is no truthful transport
    for them. A regular file's recorded mode is its full regular-file
    `st_mode` (`S_IFREG | permissions`). Baseline links stay opaque records.
    """
    tree = {
        "format": TREE,
        "repository": repository,
        "anchor_head": anchor_head,
        "path_policy_ref": dict(policy_ref),
        "entries": _entries(view, payload_refs),
        "baseline_links": baseline_links(dict(view)),
    }
    return validate_tree(tree)


def _entries(
    view: Mapping[str, object], payload_refs: Mapping[str, Mapping[str, object]]
) -> list[dict[str, object]]:
    """Sorted regular-file entries; gitlinks and special source entries hold."""
    if view["gitlinks"]:
        raise CicdError("gitlink_source_entry")
    if view["special"]:
        raise CicdError("special_source_entry")
    source = cast(dict[str, dict[str, object]], view["source"])
    return [
        {
            "path": path,
            "mode": stat.S_IFREG | cast(int, source[path]["mode"]),
            "sha256": source[path]["sha256"],
            "payload_ref": dict(payload_refs[path]),
        }
        for path in sorted(source)
    ]


def tree_delta(
    base: Mapping[str, object], candidate: Mapping[str, object]
) -> dict[str, object]:
    """Sorted exact per-path changes from one tree manifest to another.

    Baseline links must be identical in both trees. A new, changed or removed
    link holds `baseline_link_changed` rather than being transported.
    """
    if base["baseline_links"] != candidate["baseline_links"]:
        raise CicdError("baseline_link_changed")
    before = {
        str(entry["path"]): entry
        for entry in cast(list[dict[str, object]], base["entries"])
    }
    after = {
        str(entry["path"]): entry
        for entry in cast(list[dict[str, object]], candidate["entries"])
    }
    changes = [
        {"path": path, "before": before.get(path), "after": after.get(path)}
        for path in sorted(set(before) | set(after))
        if before.get(path) != after.get(path)
    ]
    delta = {
        "format": DELTA,
        "base_tree_sha256": tree_digest(dict(base)),
        "candidate_tree_sha256": tree_digest(dict(candidate)),
        "changes": changes,
    }
    return validate_delta(delta)


def apply_job_delta(
    prior: Mapping[str, object],
    job_delta: Mapping[str, object],
    foreign_dirty_paths: Iterable[str],
) -> dict[str, object]:
    """Candidate tree: the prior accepted logical tree plus only this job's delta.

    `job_delta` compares the job's frozen start with its stopped end, so it
    never packages the whole dirty workarea. A changed path that held foreign
    preexisting edits refuses `foreign_overlap`, because hunk ownership is
    never guessed. Each change must start from the prior logical tree's exact
    entry, else `logical_base_mismatch`. Unchanged foreign dirty files stay
    out of the cumulative tree.
    """
    foreign = set(foreign_dirty_paths)
    entries = {
        str(entry["path"]): entry
        for entry in cast(list[dict[str, object]], prior["entries"])
    }
    for change in cast(list[dict[str, object]], job_delta["changes"]):
        path = str(change["path"])
        if path in foreign:
            raise CicdError("foreign_overlap")
        if entries.get(path) != change["before"]:
            raise CicdError("logical_base_mismatch")
        if change["after"] is None:
            del entries[path]
        else:
            entries[path] = cast(dict[str, object], change["after"])
    tree = dict(prior) | {"entries": [entries[path] for path in sorted(entries)]}
    return validate_tree(tree)


def _current(target: Path, path: str) -> dict[str, object] | None | str:
    """Exact regular-file facts, None for absent, or a reason for any other shape."""
    current = target
    for part in Path(path).parts[:-1]:
        current = current / part
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            return None
        if not stat.S_ISDIR(info.st_mode):
            return "link_parent"
    try:
        info = os.lstat(target / path)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        return "not_regular_file"
    descriptor = os.open(target / path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            content = hashlib.sha256(stream.read()).hexdigest()
    finally:
        os.close(descriptor)
    return {"mode": stat.S_IFREG | stat.S_IMODE(info.st_mode), "sha256": content}


def _facts(entry: Mapping[str, object] | None) -> dict[str, object] | None:
    return None if entry is None else {"mode": entry["mode"], "sha256": entry["sha256"]}


def _temporary(target: Path, path: str) -> Path:
    return (target / path).with_name("." + Path(path).name + ".cicd-tmp")


def apply_delta(
    target: Path,
    delta: Mapping[str, object],
    bundle: Path,
    *,
    inverse: bool = False,
) -> dict[str, list[str]]:
    """Move every delta path from its exact source image to its exact destination.

    Import applies before → after, and restore (`inverse`) applies after →
    before. Each path must currently be exactly its source (pending) or
    exactly its destination (already applied, e.g. after a partial crash).
    Anything else (a third value, a link or other non-file, a write through a
    linked parent, a missing or mismatched payload) holds before any write.
    No broad cleanup, checkout or reset ever happens.
    """
    pending: list[tuple[str, Mapping[str, object] | None, bytes | None]] = []
    already: list[str] = []
    for change in cast(list[dict[str, object]], delta["changes"]):
        path = str(change["path"])
        first, second = ("after", "before") if inverse else ("before", "after")
        source = cast(dict[str, object] | None, change[first])
        destination = cast(dict[str, object] | None, change[second])
        current = _current(target, path)
        if isinstance(current, str):
            raise CicdError(current)
        if current == _facts(destination):
            already.append(path)
            continue
        if current != _facts(source):
            raise CicdError("materialization_drift")
        raw = None
        if destination is not None:
            ref = cast(dict[str, object], destination["payload_ref"])
            try:
                raw = (bundle / str(ref["path"])).read_bytes()
            except OSError as exc:
                raise CicdError("payload_missing") from exc
            if hashlib.sha256(raw).hexdigest() != destination["sha256"]:
                raise CicdError("payload_mismatch")
        pending.append((path, destination, raw))
    for path, destination, _ in pending:
        # A leftover temporary from an interrupted write is never reused blindly.
        if destination is not None and _temporary(target, path).exists():
            raise CicdError("stale_write_intent")
    for path, destination, raw in pending:
        if destination is None:
            os.unlink(target / path)
            continue
        (target / path).parent.mkdir(parents=True, exist_ok=True)
        temporary = _temporary(target, path)
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(cast(bytes, raw))
                stream.flush()
                os.fsync(stream.fileno())
            os.fchmod(descriptor, stat.S_IMODE(cast(int, destination["mode"])))
        finally:
            os.close(descriptor)
        os.replace(temporary, target / path)
    return {"applied": [path for path, _, _ in pending], "already": already}


def target_record(root: Path, owner_ref: Mapping[str, object]) -> dict[str, object]:
    """`{root, filesystem_identity, owner_ref}`: provenance, never write authority."""
    return {
        "root": str(root),
        "filesystem_identity": filesystem_identity(root),
        "owner_ref": dict(owner_ref),
    }


def before_manifest(
    view: Mapping[str, object],
    *,
    target: Mapping[str, object],
    anchor_head: str,
    logical_base_id: str,
    policy_ref: Mapping[str, object],
    environment_ref: Mapping[str, object],
    foreign_dirty: list[str],
    payload_refs: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    """`millrace-plus-cicd-before/v1`: the exact task-start source facts.

    Entries and baseline links use the same shapes as the tree manifest.
    `source_sha256` is the classified source identity, so product and
    environment bytes never contribute. The environment is referenced only as
    its separate opaque observation.
    """
    record = {
        "format": BEFORE,
        "target": dict(target),
        "anchor_head": anchor_head,
        "logical_base_id": logical_base_id,
        "path_policy_ref": dict(policy_ref),
        "environment_ref": dict(environment_ref),
        "source_sha256": source_identity(dict(view)),
        "foreign_dirty_paths": sorted(foreign_dirty),
        "entries": _entries(view, payload_refs),
        "baseline_links": baseline_links(dict(view)),
    }
    return validate_before(record)


def bind_invocation(
    execution_home: Path,
    expected_plane: str,
    expected_node: str,
    script: Path,
    pid: int,
    cwd: Path,
) -> dict[str, object]:
    """Bind the actual caller to public Core facts, within its own deadline.

    ``execution.json`` is the consuming Plus home's bind-once descriptor. It
    pins the instance, project, host settings and captured control bytes. A
    reserved PID is re-observed at the captured idle cadence, without another
    watchdog. The retained observation is evidence, never a private store read.
    """
    import time

    from .local_intake import strict_json
    from .router import control

    execution_home = execution_home.resolve(strict=True)
    descriptor_path = execution_home / "execution.json"
    if descriptor_path.is_symlink():
        raise CicdError("execution_descriptor_changed")
    descriptor = strict_json(descriptor_path.read_bytes())
    if not isinstance(descriptor, dict) or descriptor.get("execution_home") != str(
        execution_home
    ):
        raise CicdError("invalid_execution_descriptor")
    state_home = Path(str(descriptor["state_home"]))
    instance = str(descriptor["instance_id"])
    controls = script.parent.parent
    relative = script.relative_to(controls).as_posix()
    expected_hashes = cast(dict[str, str], descriptor["control_hashes"])
    if script.is_symlink() or hashlib.sha256(
        script.read_bytes()
    ).hexdigest() != expected_hashes.get(relative):
        raise CicdError("captured_control_changed")
    from .cicd_v2_runtime import _read

    for relative_name, expected in expected_hashes.items():
        if hashlib.sha256(_read(controls / relative_name)).hexdigest() != expected:
            raise CicdError("captured_control_changed")
    while True:
        observed = control.inspect_instance(instance, state_home=state_home)
        if (
            observed.get("project_root") != descriptor["project_root"]
            or observed.get("host_paths") != descriptor["host_paths"]
        ):
            raise CicdError("instance_settings_changed")
        runs = [
            row
            for row in cast(list[dict[str, object]], observed["runs"])
            if row.get("status") not in {"succeeded", "failed", "cancelled"}
        ]
        if len(runs) != 1 or runs[0].get("status") != "running":
            raise CicdError("run_not_running")
        view = control.inspect_run(instance, str(runs[0]["id"]), state_home=state_home)
        validation_ref = descriptor["validation_ref"]
        raw_validation = _read(execution_home / "project/main" / validation_ref["path"])
        if hashlib.sha256(raw_validation).hexdigest() != validation_ref["sha256"]:
            raise CicdError("captured_validation_changed")
        validation = strict_json(raw_validation)
        draft = view.get("draft_revision", {})
        if (
            validation.get("valid") is not True
            or draft.get("diagnostic") is not None
            or draft.get("files") != validation["draft_revision"]["files"]
            or draft.get("revision_id") != view.get("revision_id")
            or view.get("revision_id") != view.get("startup_revision_id")
        ):
            raise CicdError("captured_revision_changed")
        plane = cast(dict[str, dict[str, object]], view["planes"])[expected_plane]
        rows = [
            row
            for row in cast(list[dict[str, object]], view["invocations"])
            if row["id"] == plane.get("active_invocation")
        ]
        if len(rows) != 1:
            raise CicdError("invocation_ambiguous")
        deadline = rows[0].get("deadline")
        if (
            isinstance(deadline, bool)
            or not isinstance(deadline, int | float)
            or not math.isfinite(deadline)
        ):
            raise CicdError("invocation_deadline_missing")
        remaining = deadline - time.time()
        if remaining <= 0:
            raise CicdError("invocation_deadline_expired")
        try:
            context = match_invocation(
                view,
                instance_id=instance,
                state_home=state_home,
                controls_root=controls,
                expected_plane=expected_plane,
                expected_node=expected_node,
                script=script,
                pid=pid,
                cwd=cwd,
            )
        except CicdError as exc:
            if exc.code != "pid_unpublished":
                raise
            time.sleep(min(float(descriptor["poll_seconds"]), remaining))
            continue
        raw = canonical(view)
        evidence = (
            execution_home
            / "project"
            / expected_plane
            / (str(context["public_run_ref"]) + ".json")
        )
        write_exact(evidence, raw)
        return context


def _tree(home, root: Path) -> dict[str, object]:
    from .cicd_source import git_head, snapshot_policy

    view = snapshot_policy(root, home.policy, home.guards)
    from .cicd_v2_records import structural_forbidden

    if any(structural_forbidden(path) for path in view["source"]):
        raise CicdError("structural_source_path_forbidden")
    payloads = retain_payloads(root, view, home.workspace)
    return tree_manifest(
        view,
        repository=str(home.config["repository"]),
        anchor_head=git_head(root),
        policy_ref=home.config["path_policy_ref"],
        payload_refs=payloads,
    )


def _candidate(home, value: object) -> dict[str, object]:
    from . import cicd_v2_records as vr

    record = vr.validate_candidate(home.read_ref(value))
    if (
        record["repository"] != home.config["repository"]
        or record["config_ref"] != home.config_ref
    ):
        raise CicdError("candidate_config_mismatch")
    tree = vr.validate_tree(home.read_ref(record["tree_manifest_ref"]))
    delta = vr.validate_delta(home.read_ref(record["delta_ref"]))
    before = vr.validate_before(home.read_ref(record["before_manifest_ref"]))
    if (
        vr.tree_digest(tree) != record["candidate_tree_sha256"]
        or delta["base_tree_sha256"] != record["base_tree_sha256"]
        or delta["candidate_tree_sha256"] != record["candidate_tree_sha256"]
        or tree["path_policy_ref"] != home.config["path_policy_ref"]
        or before["path_policy_ref"] != home.config["path_policy_ref"]
    ):
        raise CicdError("candidate_manifest_mismatch")
    reconstructed = {str(entry["path"]): entry for entry in tree["entries"]}
    for change in delta["changes"]:
        if reconstructed.get(change["path"]) != change["after"]:
            raise CicdError("candidate_delta_mismatch")
        if change["before"] is None:
            reconstructed.pop(change["path"], None)
        else:
            reconstructed[change["path"]] = change["before"]
    base = tree | {"entries": [reconstructed[path] for path in sorted(reconstructed)]}
    if (
        vr.tree_digest(base) != record["base_tree_sha256"]
        or before["baseline_links"] != tree["baseline_links"]
    ):
        raise CicdError("candidate_delta_mismatch")
    for entry in cast(list[dict[str, object]], tree["entries"]):
        if _classify(home, str(entry["path"])) != "source":
            raise CicdError("candidate_path_policy_mismatch")
        raw = home.resolve_ref(entry["payload_ref"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            raise CicdError("payload_mismatch")
    target = cast(dict[str, object], record["verification_target"])
    if (
        target["root"] != str(home.target)
        or target["filesystem_identity"] != home.target_identity
        or target["owner_ref"] != home.record["owner_ref"]
    ):
        raise CicdError("candidate_target_mismatch")
    for item in cast(list[object], record["origin_refs"]):
        home.resolve_ref(item)
    if record["assessment_ref"] is not None:
        home.resolve_ref(record["assessment_ref"])
    return record


def _facade_context(
    home,
    invocation: Mapping[str, object],
    controls: Path,
    operation: str,
    *,
    request: Mapping[str, object] | None = None,
) -> dict[str, object]:
    from . import cicd_v2_records as vr
    from .cicd_v2_runtime import _object, _read

    actors = cast(list[dict[str, object]], home.authority["actors"])
    matched = []
    for actor in actors:
        execution_home = Path(str(actor["execution_home"]))
        descriptor = _object(_read(execution_home / "execution.json"))
        if descriptor["instance_id"] == invocation["instance_id"]:
            matched.append((actor, execution_home, descriptor))
    if len(matched) != 1:
        raise CicdError("actor_not_authorized")
    actor, execution_home, descriptor = matched[0]
    if operation not in home.authority["operations"]:
        raise CicdError("operation_not_authorized")
    binding = _object(_read(execution_home / "project" / "main" / "actor-binding.json"))
    vr.verify_actor_binding(home.authority, binding)
    if (
        binding["instance_id"] != invocation["instance_id"]
        or binding["definition_ref"] != actor["definition_ref"]
        or descriptor["state_home"] != invocation["state_home"]
    ):
        raise CicdError("actor_binding_mismatch")
    scripts = [
        Path(str(arg))
        for arg in cast(list[object], invocation["argv"])
        if str(arg).startswith(str(controls) + "/") and str(arg).endswith(".py")
    ]
    if len(scripts) != 1:
        raise CicdError("invocation_argv_mismatch")
    fresh = bind_invocation(
        execution_home,
        str(invocation["plane_id"]),
        str(invocation["node"]),
        scripts[0],
        os.getpid(),
        Path.cwd(),
    )
    if any(
        fresh[key] != invocation[key]
        for key in CONTEXT_FIELDS
        if key != "public_run_ref"
    ):
        raise CicdError("invocation_context_changed")
    if request is not None:
        if request["authority_ref"] != home.config["local_authority_ref"]:
            raise CicdError("authority_mismatch")
        if request["approval_ref"] != home.config["local_authority_ref"]:
            raise CicdError("approval_mismatch")
        home.resolve_ref(request["approval_ref"])
    return fresh


def _completed(
    home,
    context: Mapping[str, object],
    completion_ref: object,
    *,
    expected_invocation: str | None = None,
    expected_attempt: int | None = None,
    expected_node: str | None = None,
) -> tuple[dict[str, object], dict[str, object]]:
    """Re-observe the producing public completion; supplied facts grant nothing."""
    from .router import control

    view = control.inspect_run(
        str(context["instance_id"]),
        str(context["run_id"]),
        state_home=Path(str(context["state_home"])),
    )
    preparers = [
        row for row in view["invocations"] if row["id"] == context["invocation_id"]
    ]
    if len(preparers) != 1 or any(
        preparers[0].get(key) != context[key]
        for key in (
            "plane_id",
            "node",
            "attempt",
            "pid",
            "cwd",
            "argv",
            "deadline",
            "revision_id",
        )
    ):
        raise CicdError("producing_context_changed")
    rows = [
        row
        for row in cast(list[dict[str, object]], view["invocations"])
        if (expected_invocation is None or row["id"] == expected_invocation)
        and (expected_attempt is None or row["attempt"] == expected_attempt)
        and (expected_node is None or row["node"] == expected_node)
        and row["plane_id"] == context["plane_id"]
    ]
    if len(rows) != 1 or rows[0].get("writer_state") != "stopped":
        raise CicdError("writer_not_stopped")
    if rows[0]["revision_id"] != context["revision_id"]:
        raise CicdError("producing_revision_changed")
    done = [
        row
        for row in cast(list[dict[str, object]], view["completions"])
        if row["invocation_id"] == rows[0]["id"]
    ]
    if len(done) != 1 or done[0].get("effective_writer") != "stopped":
        raise CicdError("completion_missing_or_unknown")
    supplied = home.read_ref(completion_ref)
    supplied_done = supplied.get("completion", supplied)
    if supplied_done != done[0]:
        raise CicdError("completion_mismatch")
    return rows[0], done[0]


def _lease(home, materialization_id: str | None = None) -> dict[str, object] | None:
    from . import cicd_v2_records as vr
    from .cicd_v2_runtime import _object, _read

    pointer = home.home / "active-materialization.json"
    if not pointer.exists():
        return None
    record = vr.validate_materialization(home.read_ref(_object(_read(pointer))))
    if (
        materialization_id is not None
        and record["materialization_id"] != materialization_id
    ):
        raise CicdError("materialization_mismatch")
    if record["owner_ref"] != home.record["owner_ref"]:
        raise CicdError("materialization_owner_mismatch")
    for ref in cast(list[object], record["operation_refs"]):
        operation = vr.validate_materialization_operation(home.read_ref(ref))
        if operation["materialization_id"] != record["materialization_id"]:
            raise CicdError("operation_lease_mismatch")
        for key in ("beforeimages_ref", "allowed_writes_ref", "write_intent_ref"):
            home.resolve_ref(operation[key])
    return record


def _save_lease(
    home, record: dict[str, object], expected: int | None
) -> dict[str, object]:
    from . import cicd_v2_records as vr
    from .cicd_source import write_replace

    current = _lease(home)
    if current is not None and current["state"] == "restored" and expected is None:
        current = None
    if (
        (current is None) != (expected is None)
        or current is not None
        and (
            current["version"] != expected
            or current["materialization_id"] != record["materialization_id"]
        )
    ):
        raise CicdError("stale_materialization")
    if current is not None and (
        record["origin_base_candidate_ref"] != current["origin_base_candidate_ref"]
        or current["candidate_ref"] is not None
        and record["candidate_ref"] != current["candidate_ref"]
    ):
        raise CicdError("materialization_binding_changed")
    saved = vr.validate_materialization(
        record | {"version": 1 if expected is None else expected + 1}
    )
    for ref in saved["operation_refs"]:
        vr.validate_materialization_operation(home.read_ref(ref))
    write_replace(
        home.home / "active-materialization.json", canonical(home.retain(saved))
    )
    return saved


def _reserve_operation(
    home,
    lease: dict[str, object],
    operation: str,
    request: Mapping[str, object],
    before: dict[str, object],
    after: dict[str, object],
    context: Mapping[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    from . import cicd_v2_records as vr

    delta = tree_delta(before, after)
    before_ref = home.retain(before)
    allowed = home.retain(
        [item["path"] for item in cast(list[dict[str, object]], delta["changes"])]
    )
    intent_ref = home.retain(
        {
            "invocation": dict(context),
            "delta_ref": home.retain(delta),
            "request": dict(request),
            "before_ref": before_ref,
            "snapshot_ref": home.retain(home.observe()),
            "after_ref": home.retain(after),
        }
    )
    op_id = vr.digest(
        {
            "request": request,
            "operation": operation,
            "lease": lease["materialization_id"],
        }
    )
    op = vr.validate_materialization_operation(
        {
            "format": vr.MATERIALIZATION_OPERATION,
            "operation_id": op_id,
            "materialization_id": lease["materialization_id"],
            "operation": operation,
            "request_sha256": vr.digest(request),
            "before_tree_sha256": vr.tree_digest(before),
            "after_tree_sha256": None,
            "beforeimages_ref": before_ref,
            "allowed_writes_ref": allowed,
            "write_intent_ref": intent_ref,
            "completion_ref": None,
            "previous_receipt_ref": None,
            "status": "reserved",
            "restoration_ref": None,
        }
    )
    saved = _save_lease(
        home,
        lease
        | {
            "operation_refs": [*lease["operation_refs"], home.retain(op)],
            "beforeimages_ref": lease["beforeimages_ref"] or before_ref,
        },
        cast(int, lease["version"]),
    )
    return saved, op


def accept_materialization_operation(
    home: V2Home,
    materialization_id: str,
    operation_id: str,
    completion_ref: object,
    expected_version: int,
) -> dict[str, object]:
    """Metadata-only following guard: append actual public stopped-writer evidence."""
    from . import cicd_v2_records as vr

    with home.lock(), home.state_lock():
        lease = _lease(home, materialization_id)
        if lease is None or lease["version"] != expected_version:
            raise CicdError("stale_materialization")
        operations = [
            vr.validate_materialization_operation(home.read_ref(ref))
            for ref in lease["operation_refs"]
        ]
        matching = [row for row in operations if row["operation_id"] == operation_id]
        if not matching:
            raise CicdError("operation_missing")
        operation = matching[-1]
        intent = home.read_ref(operation["write_intent_ref"])
        context = cast(dict[str, object], intent["invocation"])
        guard = _active_guard(
            home,
            context,
            {
                "prepare_base": "prepare-base",
                "repair": "project-role",
                "check_products": "check",
                "import": "import",
                "restore": "restore",
            }[operation["operation"]],
        )
        if operation["operation"] == "repair":
            invocation, done = _completed(
                home,
                context,
                completion_ref,
                expected_attempt=intent["expected_attempt"],
                expected_node=intent["expected_node"],
            )
        else:
            invocation, done = _completed(
                home,
                context,
                completion_ref,
                expected_invocation=str(context["invocation_id"]),
            )
        actual = _tree(home, home.target)
        if operation["operation"] == "repair":
            _validate_repair_delta(home, operation, intent, actual, done)
            home.retain(actual)
        else:
            expected = vr.validate_tree(home.read_ref(intent["after_ref"]))
            if actual != expected:
                raise CicdError("materialization_delta_unaccounted")
        if operation["status"] in {"applied", "restored"}:
            if operation["completion_ref"] != completion_ref:
                raise CicdError("operation_completion_changed")
            return lease
        if operation["operation"] == "restore":
            from .cicd_v2_runtime import _object, _read

            plan = _object(
                _read(
                    home.workspace
                    / (str(operation["operation_id"]) + ".restore-plan.json")
                )
            )
            if home.observe()["products"] != plan["products_after"]:
                raise CicdError("product_restoration_unaccounted")
        if operation["operation"] == "check_products":
            _accept_candidate_check(
                home, operation, invocation, done, completion_ref, guard
            )
        restoration_ref = None
        if operation["operation"] == "restore":
            restoration_ref = home.retain(
                {
                    "operation_ref": home.retain(operation),
                    "completion_ref": completion_ref,
                    "plan_ref": home.file_ref(
                        home.workspace
                        / (str(operation["operation_id"]) + ".restore-plan.json")
                    ),
                    "target_after_sha256": source_identity(home.observe()),
                }
            )
        after = operation | {
            "after_tree_sha256": vr.tree_digest(actual),
            "completion_ref": completion_ref,
            "previous_receipt_ref": home.retain(operation),
            "status": "restored" if restoration_ref is not None else "applied",
            "restoration_ref": restoration_ref,
        }
        vr.validate_materialization_operation(after)
        state = (
            "restored"
            if restoration_ref is not None
            else "base_ready"
            if operation["operation"] == "prepare_base"
            else lease["state"]
        )
        return _save_lease(
            home,
            lease
            | {
                "operation_refs": [*lease["operation_refs"], home.retain(after)],
                "target_after_sha256": source_identity(home.observe()),
                "state": state,
                "restoration_ref": restoration_ref,
            },
            expected_version,
        )


def accept_materialization(
    home: V2Home,
    materialization_id: str,
    candidate_ref: object,
    completion_refs: list[object],
    expected_version: int,
) -> dict[str, object]:
    """Bind once after exact mutation completion and complete actual tree accounting."""
    from . import cicd_v2_records as vr

    with home.lock(), home.state_lock():
        lease = _lease(home, materialization_id)
        if lease is None or lease["version"] != expected_version:
            raise CicdError("stale_materialization")
        candidate = _candidate(home, candidate_ref)
        operations = [
            vr.validate_materialization_operation(home.read_ref(ref))
            for ref in lease["operation_refs"]
        ]
        current = {str(row["operation_id"]): row for row in operations}
        mutations = [
            row for row in current.values() if row["operation"] in {"import", "repair"}
        ]
        if not mutations:
            raise CicdError("mutation_completion_missing")
        for operation in current.values():
            if (
                operation["status"] != "applied"
                or operation["completion_ref"] not in completion_refs
            ):
                raise CicdError("mutation_unaccounted")
            intent = home.read_ref(operation["write_intent_ref"])
            context = cast(dict[str, object], intent["invocation"])
            _active_guard(
                home,
                context,
                "import" if operation["operation"] == "import" else "project-role",
            )
            _completed(
                home,
                context,
                operation["completion_ref"],
                expected_attempt=intent.get("expected_attempt")
                if operation["operation"] == "repair"
                else None,
                expected_node=intent.get("expected_node")
                if operation["operation"] == "repair"
                else None,
                expected_invocation=None
                if operation["operation"] == "repair"
                else str(context["invocation_id"]),
            )
        actual = _tree(home, home.target)
        if vr.tree_digest(actual) != candidate["candidate_tree_sha256"]:
            raise CicdError("candidate_tree_mismatch")
        if (
            lease["candidate_ref"] is not None
            and lease["candidate_ref"] != candidate_ref
        ):
            raise CicdError("candidate_already_bound")
        return _save_lease(
            home,
            lease
            | {
                "candidate_ref": candidate_ref,
                "target_after_sha256": source_identity(home.observe()),
                "state": "sealed",
            },
            expected_version,
        )


def _source_completion(home, request: Mapping[str, object], invocation_id: str) -> None:
    """Require explicit, re-observed producing context; never use a latest run."""
    for ref in cast(list[object], request["evidence_refs"]):
        evidence = home.read_ref(ref)
        context = evidence.get("invocation_context")
        completion_ref = evidence.get("completion_ref")
        if (
            isinstance(context, dict)
            and completion_ref is not None
            and context.get("invocation_id") == invocation_id
        ):
            _completed(home, context, completion_ref, expected_invocation=invocation_id)
            return
    raise CicdError("source_completion_missing")


def _export(home, request: Mapping[str, object]) -> dict[str, object]:
    from . import cicd_v2_records as vr

    basis = vr.validate_export_basis(home.read_ref(request["export_basis_ref"]))
    if (
        basis["subject_ref"] != request["subject_ref"]
        or basis["config_ref"] != home.config_ref
        or basis["authority_ref"] != home.config["local_authority_ref"]
    ):
        raise CicdError("export_basis_mismatch")
    _source_completion(home, request, str(basis["writer_facts"]["core_invocation_id"]))
    source = cast(dict[str, object], basis["source_target"])
    from .cicd_source import canonical_dir

    before = vr.validate_before(home.read_ref(basis["before_manifest_ref"]))
    observed = vr.validate_tree(home.read_ref(basis["source_after_manifest_ref"]))
    leased_evidence = []
    physical = source
    if before["target"] == basis["verification_target"]:
        leased_evidence = _leased_export_origin(home, request, basis, before, observed)
        physical = cast(dict[str, object], basis["verification_target"])
    elif before["target"] != source:
        raise CicdError("export_physical_root_mismatch")
    root = canonical_dir(str(physical["root"]), "invalid_source_target")
    if filesystem_identity(root) != physical["filesystem_identity"]:
        raise CicdError("source_identity_changed")
    actual = _tree(home, root)
    if actual != observed or observed["anchor_head"] != basis["anchor_head"]:
        raise CicdError("export_source_changed")
    initial = {
        "format": vr.TREE,
        "repository": observed["repository"],
        "anchor_head": before["anchor_head"],
        "path_policy_ref": before["path_policy_ref"],
        "entries": before["entries"],
        "baseline_links": before["baseline_links"],
    }
    vr.validate_tree(initial)
    prior_ref = basis["prior_candidate_ref"]
    if prior_ref is None:
        # The explicit bootstrap is a real anchor record, not a fabricated task.
        subject = vr.validate_anchor(home.read_ref(request["subject_ref"]))
        if (
            subject["tree_ref"] != basis["source_after_manifest_ref"]
            or initial != observed
            or before["foreign_dirty_paths"]
        ):
            raise CicdError("anchor_baseline_mismatch")
        prior = observed
        origin_refs = [request["subject_ref"]]
    else:
        subject = home.read_ref(request["subject_ref"])
        if subject.get("format") != vr.INPUT:
            raise CicdError("factory_subject_validator_unavailable")
        vr.validate_input(subject)
        assessment = vr.validate_assessment(home.read_ref(basis["assessment_ref"]))
        if (
            assessment["subject_ref"] != request["subject_ref"]
            or assessment["route"] != "REPAIR_ALLOWED"
        ):
            raise CicdError("assessment_subject_mismatch")
        previous = _candidate(home, prior_ref)
        if basis["logical_base_id"] != previous["candidate_tree_sha256"]:
            raise CicdError("logical_base_mismatch")
        prior = vr.validate_tree(home.read_ref(previous["tree_manifest_ref"]))
        origin_refs = [
            request["subject_ref"],
            prior_ref,
            request["export_basis_ref"],
            *leased_evidence,
        ]
    job_delta = tree_delta(initial, observed)
    cumulative = apply_job_delta(
        prior, job_delta, cast(list[str], before["foreign_dirty_paths"])
    )
    delta = tree_delta(prior, cumulative)
    if basis["logical_base_id"] != vr.tree_digest(prior):
        raise CicdError("logical_base_mismatch")
    record = {
        "format": vr.CANDIDATE,
        "candidate_id": "0" * 64,
        "origin_refs": origin_refs,
        "repository": home.config["repository"],
        "anchor_head": basis["anchor_head"],
        "logical_base_id": basis["logical_base_id"],
        "prior_candidate_id": None
        if prior_ref is None
        else _candidate(home, prior_ref)["candidate_id"],
        "base_tree_sha256": vr.tree_digest(prior),
        "candidate_tree_sha256": vr.tree_digest(cumulative),
        "delta_ref": home.retain(delta),
        "tree_manifest_ref": home.retain(cumulative),
        "before_manifest_ref": basis["before_manifest_ref"],
        "source_target": source,
        "verification_target": basis["verification_target"],
        "config_ref": home.config_ref,
        "assessment_ref": basis["assessment_ref"],
    }
    record["candidate_id"] = vr.candidate_identity(record)
    return vr.validate_candidate(record)


def operate_candidate(
    home: V2Home, request: object, controls: Path, invocation: Mapping[str, object]
) -> dict[str, object]:
    """Perform exactly one selected local operation; never dispatch another node."""
    from . import cicd_v2_records as vr
    from .cicd_v2_runtime import _object, _read

    selected = vr.validate_candidate_request(request)
    operation = str(selected["operation"])
    writing = (
        operation in vr.TARGET_WRITING_OPERATIONS
        or operation == "project-role"
        and selected["role"] == "repair"
    )
    context = _facade_context(
        home, invocation, controls, operation, request=selected if writing else None
    )
    key = vr.digest(selected["request_id"])
    request_path = home.workspace / (key + ".candidate-request.json")
    result_path = home.workspace / (key + ".candidate-result.json")
    with home.lock(), home.state_lock():
        if request_path.exists() and _read(request_path) != canonical(selected):
            raise CicdError("candidate_request_conflict")
        if result_path.exists():
            if not request_path.exists():
                raise CicdError("candidate_request_missing")
            return vr.validate_candidate_result(_object(_read(result_path)))
        if request_path.exists():
            if operation == "import":
                return _resume_import(home, selected, context, result_path)
            raise CicdError("candidate_operation_interrupted")
        _prior_roles_closed(home)
        before_sha = source_identity(home.observe())
        if (
            selected["expected_target_sha256"] is not None
            and selected["expected_target_sha256"] != before_sha
        ):
            raise CicdError("target_basis_mismatch")
        lease = (
            _lease(home, selected["materialization_id"])
            if selected["materialization_id"] is not None or operation == "prepare-base"
            else None
        )
        if selected["materialization_id"] is not None and (
            lease is None
            or lease["version"] != selected["expected_materialization_version"]
        ):
            raise CicdError("stale_materialization")
        candidate = (
            None
            if selected["candidate_ref"] is None
            else _candidate(home, selected["candidate_ref"])
        )
        write_exact(request_path, canonical(selected), "candidate_request_conflict")
        artifacts = []
        if operation == "export":
            candidate = _export(home, selected)
            artifacts = [home.retain(candidate)]
        elif operation == "prepare-base":
            if lease is not None and lease["state"] != "restored":
                raise CicdError("target_lease_active")
            if any(
                row["status"] in {"claimed", "running", "held"}
                for row in home.intake.tasks()
            ):
                raise CicdError("active_input")
            actual = _tree(home, home.target)
            destination = vr.validate_tree(
                home.read_ref(candidate["tree_manifest_ref"])
            )
            if actual["baseline_links"] != destination["baseline_links"]:
                raise CicdError("baseline_links_changed")
            # Only a clean validation anchor may become a new lease's base.
            # Environment paths remain opaque; source dirt is never adopted.
            import time

            from .cicd_source import foreign_dirty_paths

            if foreign_dirty_paths(
                home.target,
                actual["anchor_head"],
                home.observe(),
                context["deadline"] - time.time(),
            ):
                raise CicdError("validation_anchor_dirty")
            if (
                candidate["prior_candidate_id"] is None
                and vr.tree_digest(actual) != candidate["base_tree_sha256"]
            ):
                raise CicdError("validation_anchor_changed")
            materialization_id = vr.digest(
                {"request": selected, "target": home.target_identity}
            )
            lease = vr.validate_materialization(
                {
                    "format": vr.MATERIALIZATION,
                    "materialization_id": materialization_id,
                    "version": 1,
                    "origin_base_candidate_ref": selected["candidate_ref"],
                    "candidate_ref": None,
                    "request_sha256": vr.digest(selected),
                    "owner_ref": home.record["owner_ref"],
                    "target_before_sha256": before_sha,
                    "target_after_sha256": None,
                    "beforeimages_ref": None,
                    "operation_refs": [],
                    "state": "reserved",
                    "restoration_ref": None,
                }
            )
            lease = _save_lease(home, lease, None)
            lease, receipt = _reserve_operation(
                home, lease, "prepare_base", selected, actual, destination, context
            )
            intent = home.read_ref(receipt["write_intent_ref"])
            apply_delta(home.target, home.read_ref(intent["delta_ref"]), home.workspace)
            artifacts = [home.retain(receipt)]
        elif operation == "import":
            _require_accounted_operations(home, lease)
            _candidate_scope(home, candidate)
            if lease["state"] != "base_ready" or lease["candidate_ref"] is not None:
                raise CicdError("materialization_not_base_ready")
            actual = _tree(home, home.target)
            if vr.tree_digest(actual) != candidate["base_tree_sha256"]:
                raise CicdError("logical_base_mismatch")
            destination = vr.validate_tree(
                home.read_ref(candidate["tree_manifest_ref"])
            )
            lease, receipt = _reserve_operation(
                home, lease, "import", selected, actual, destination, context
            )
            intent = home.read_ref(receipt["write_intent_ref"])
            apply_delta(home.target, home.read_ref(intent["delta_ref"]), home.workspace)
            artifacts = [home.retain(receipt)]
        elif operation == "restore":
            lease, receipt = _restore_candidate(home, selected, context, lease)
            artifacts = [home.retain(receipt)]
        elif operation == "project-role":
            if (
                selected["role_binding_id"]
                != home.config["role_bindings"][selected["role"]]["id"]
            ):
                raise CicdError("role_binding_mismatch")
            basis_refs = [home.read_ref(ref) for ref in selected["evidence_refs"]]
            bases = [
                value for value in basis_refs if value.get("format") == vr.ROLE_BASIS
            ]
            if len(bases) != 1:
                raise CicdError("role_basis_missing")
            projection = _project_role_locked(
                home,
                selected["subject_ref"],
                str(selected["role"]),
                bases[0],
                controls,
                context,
            )
            artifacts = [home.retain(projection)]
            lease = _lease(home, selected["materialization_id"])
        elif operation == "review":
            projection = _prepare_review_locked(home, selected, controls, context)
            artifacts = [home.retain(projection)]
        elif operation == "check":
            return _check_candidate(home, selected, controls, context)
        elif operation == "verify":
            verification = _verify_candidate(home, selected, controls, context)
            artifacts = [home.retain(verification)]
        else:
            raise CicdError("invalid_candidate_operation")
        result = vr.validate_candidate_result(
            {
                "format": vr.CANDIDATE_RESULT,
                "request_id": selected["request_id"],
                "request_sha256": vr.digest(selected),
                "operation": operation,
                "candidate_id": None
                if candidate is None
                else candidate["candidate_id"],
                "materialization_id": None
                if lease is None
                else lease["materialization_id"],
                "materialization_version": None if lease is None else lease["version"],
                "status": "completed",
                "target_before_sha256": before_sha,
                "target_after_sha256": source_identity(home.observe()),
                "artifact_refs": artifacts,
                "writer_facts": {
                    "core_invocation_id": context["invocation_id"],
                    "writer_state": "may_live",
                },
                "refusal": None,
            }
        )
        write_exact(result_path, canonical(result), "candidate_result_conflict")
        return result


def _resume_import(
    home,
    request: Mapping[str, object],
    context: Mapping[str, object],
    result_path: Path,
) -> dict[str, object]:
    """Complete one interrupted exact import intent; preserve its operation ID."""
    from . import cicd_v2_records as vr

    lease = _lease(home, request["materialization_id"])
    if (
        lease is None
        or lease["state"] != "base_ready"
        or lease["candidate_ref"] is not None
    ):
        raise CicdError("materialization_not_base_ready")
    operations = {
        home.read_ref(ref)["operation_id"]: (ref, home.read_ref(ref))
        for ref in lease["operation_refs"]
    }
    matching = [
        (ref, row)
        for ref, row in operations.values()
        if row["operation"] == "import"
        and row["request_sha256"] == vr.digest(request)
        and row["status"] in {"reserved", "unknown"}
    ]
    if len(matching) != 1 or any(
        row["status"] != "applied"
        and row["operation_id"] != matching[0][1]["operation_id"]
        for _, row in operations.values()
    ):
        raise CicdError("import_reservation_unknown")
    previous_ref, operation = matching[0]
    intent = home.read_ref(operation["write_intent_ref"])
    if intent["request"] != request:
        raise CicdError("candidate_request_conflict")
    producer = intent["invocation"]
    from .router import control

    prior = control.inspect_run(
        producer["instance_id"],
        producer["run_id"],
        state_home=Path(producer["state_home"]),
    )
    done = [
        row
        for row in prior["completions"]
        if row["invocation_id"] == producer["invocation_id"]
    ]
    if len(done) != 1:
        raise CicdError("import_writer_unknown")
    completion_ref = home.retain(done[0])
    _completed(
        home, producer, completion_ref, expected_invocation=producer["invocation_id"]
    )
    candidate = _candidate(home, request["candidate_ref"])
    _candidate_scope(home, candidate)
    before = vr.validate_tree(home.read_ref(intent["before_ref"]))
    after = vr.validate_tree(home.read_ref(intent["after_ref"]))
    actual = _tree(home, home.target)
    if (
        actual["anchor_head"] != before["anchor_head"]
        or actual["baseline_links"] != before["baseline_links"]
    ):
        raise CicdError("import_foreign_drift")
    old, new, current = (
        {row["path"]: row for row in tree["entries"]}
        for tree in (before, after, actual)
    )
    if any(
        current.get(path) not in (old.get(path), new.get(path))
        for path in set(old) | set(new) | set(current)
    ):
        raise CicdError("import_foreign_drift")
    if home.observe()["products"] != home.read_ref(intent["snapshot_ref"])["products"]:
        raise CicdError("noncheck_product_write")
    # Append the continuation writer before touching any remaining path.
    continued = operation | {
        "status": "unknown",
        "previous_receipt_ref": previous_ref,
        "write_intent_ref": home.retain(
            intent
            | {
                "invocation": dict(context),
                "previous_intent_ref": operation["write_intent_ref"],
                "prior_completion_ref": completion_ref,
            }
        ),
    }
    vr.validate_materialization_operation(continued)
    lease = _save_lease(
        home,
        lease | {"operation_refs": [*lease["operation_refs"], home.retain(continued)]},
        lease["version"],
    )
    apply_delta(home.target, home.read_ref(intent["delta_ref"]), home.workspace)
    if _tree(home, home.target) != after:
        raise CicdError("materialization_delta_unaccounted")
    result = vr.validate_candidate_result(
        {
            "format": vr.CANDIDATE_RESULT,
            "request_id": request["request_id"],
            "request_sha256": vr.digest(request),
            "operation": "import",
            "candidate_id": candidate["candidate_id"],
            "materialization_id": lease["materialization_id"],
            "materialization_version": lease["version"],
            "status": "completed",
            "target_before_sha256": request["expected_target_sha256"],
            "target_after_sha256": source_identity(home.observe()),
            "artifact_refs": [home.retain(continued)],
            "writer_facts": {
                "core_invocation_id": context["invocation_id"],
                "writer_state": "may_live",
            },
            "refusal": None,
        }
    )
    write_exact(result_path, canonical(result), "candidate_result_conflict")
    return result


def _role_subject(home, subject_ref: object) -> dict[str, object]:
    from . import cicd_v2_records as vr

    record = home.read_ref(subject_ref)
    if record.get("format") != vr.INPUT:
        raise CicdError("factory_subject_validator_unavailable")
    subject = vr.validate_input(record)
    if home.input(str(subject["input_id"])) != subject:
        raise CicdError("subject_intake_mismatch")
    home.resolve_ref(subject["original_ref"])
    return subject


def _project_role_locked(
    home,
    subject_ref: object,
    role: str,
    basis: object,
    controls: Path,
    context: Mapping[str, object],
) -> dict[str, object]:
    import uuid

    from . import cicd_v2_records as vr

    subject = _role_subject(home, subject_ref)
    checked = vr.validate_role_basis(basis, role=role)
    if checked["config_sha256"] != home.config_sha256 or checked[
        "source_before_sha256"
    ] != source_identity(home.observe()):
        raise CicdError("role_basis_mismatch")
    binding = vr.validate_source_binding(home.read_ref(checked["source_binding_ref"]))
    if binding["input_ref"] != subject_ref or binding["status"] != "bound":
        raise CicdError("role_source_binding_mismatch")
    for key in (
        "subject_schema_ref",
        "source_binding_ref",
        "assessment_ref",
        "criteria_ref",
        "scope_ref",
    ):
        if checked[key] is not None:
            home.resolve_ref(checked[key])
    selection = cast(dict[str, dict[str, object]], home.config["role_bindings"])[role]
    if selection["id"] not in home.authority["operations"]:
        raise CicdError("binding_not_authorized")
    descriptor = home.read_ref(home.record["definition_ref"])
    schema_ref = checked["subject_schema_ref"]
    if schema_ref["path"] not in descriptor["control_files"]:
        raise CicdError("subject_schema_not_captured")
    active_lease = _lease(home)
    if active_lease is not None and active_lease["state"] != "restored":
        _require_accounted_operations(home, active_lease)
    if role == "repair":
        assessment = vr.validate_assessment(home.read_ref(checked["assessment_ref"]))
        if (
            assessment["subject_ref"] != subject_ref
            or assessment["route"] != "REPAIR_ALLOWED"
        ):
            raise CicdError("repair_assessment_mismatch")
        lease = _lease(home)
        if (
            lease is None
            or lease["state"] != "base_ready"
            or lease["candidate_ref"] is not None
        ):
            raise CicdError("materialization_not_base_ready")
        _require_accounted_operations(home, lease)
        _reproduction_for_role(home, lease, checked, assessment)
        _accepted_role_assessment(home, checked, subject_ref)
        # Exact source and permitted-write intent precede the native edge.
        before = _tree(home, home.target)
        scope = home.read_ref(checked["scope_ref"])
        allowed_paths = cast(
            list[str], scope.get("paths", scope.get("proposed_paths", []))
        )
        if not allowed_paths or allowed_paths != assessment["proposed_paths"]:
            raise CicdError("repair_scope_mismatch")
        for path in allowed_paths:
            if (
                vr.structural_forbidden(path)
                or _classify(home, path) != "source"
                or not any(
                    path == prefix or path.startswith(prefix + "/")
                    for prefix in home.authority["repair_paths"]
                )
                or not any(
                    path == prefix or path.startswith(prefix + "/")
                    for prefix in home.config["repair_paths"]
                )
                or any(
                    path == prefix or path.startswith(prefix + "/")
                    for prefix in home.config["protected_paths"]
                )
            ):
                raise CicdError("repair_scope_forbidden")
    attempt_id = uuid.uuid4().hex
    basis_sha = vr.digest(checked)
    role_input = {
        "format": "millrace-plus-cicd-role-input/v2",
        "input_id": subject["input_id"],
        "subject_ref": subject_ref,
        "role": role,
        "attempt_id": attempt_id,
        "basis_sha256": basis_sha,
        "source_before_sha256": checked["source_before_sha256"],
        "config_ref": home.config_ref,
        "target_root": str(home.target),
        "source_binding_ref": checked["source_binding_ref"],
        "assessment_ref": checked["assessment_ref"],
        "criteria_ref": checked["criteria_ref"],
        "scope_ref": checked["scope_ref"],
        "reproduction_refs": checked["reproduction_refs"],
        "output": attempt_id + ".result.json",
        "prompt_ref": selection["prompt_ref"],
        "skill_ref": selection["skill_ref"],
        "result_schema_ref": selection["result_schema_ref"],
    }
    projection = vr.validate_subject_projection(
        {
            "format": vr.SUBJECT_PROJECTION,
            "subject_ref": subject_ref,
            "subject_kind": "ci_input",
            "subject_id": subject["input_id"],
            "role": role,
            "attempt_id": attempt_id,
            "binding_id": selection["id"],
            "input_ref": home.retain(role_input),
            "prompt_ref": selection["prompt_ref"],
            "skill_ref": selection["skill_ref"],
            "result_schema_ref": selection["result_schema_ref"],
            "basis_sha256": basis_sha,
            "criteria_ref": checked["criteria_ref"],
            "source_binding_ref": checked["source_binding_ref"],
        }
    )
    attempt = vr.validate_attempt(
        {
            "format": vr.ATTEMPT,
            "input_id": subject["input_id"],
            "subject_ref": subject_ref,
            "attempt_id": attempt_id,
            "parent_attempt_id": None,
            "kind": role,
            "basis_sha256": basis_sha,
            "source_sha256": checked["source_before_sha256"],
            "config_sha256": home.config_sha256,
            "changed_cause_ref": None,
            "reservation": {
                "core_invocation_id": None,
                "core_attempt_id": None,
                "request_id": attempt_id,
            },
            "completion_ref": None,
            "artifact_ref": None,
            "status": "reserved",
        }
    )
    write_exact(
        home.workspace / (attempt_id + ".facade-role.json"),
        canonical(
            {
                "attempt_ref": home.retain(attempt),
                "projection_ref": home.retain(projection),
                "preparer": dict(context),
                "basis_ref": home.retain(checked),
                "expected_attempt": context["attempt"] + 1,
                "expected_node": _selected_role_node(home, context, selection, role),
                "snapshot_ref": home.retain(home.observe()),
            }
        ),
    )
    pointer = vr.validate_active_role(
        {
            "format": vr.ACTIVE_ROLE,
            "subject_id": subject["input_id"],
            "role": role,
            "attempt_id": attempt_id,
            "input_ref": projection["input_ref"],
            "projection_ref": home.retain(projection),
            "basis_sha256": basis_sha,
        }
    )
    if role == "repair":
        operation_id = vr.digest(
            {"projection": projection, "lease": lease["materialization_id"]}
        )
        before_ref = home.retain(before)
        snapshot_ref = home.retain(home.observe())
        intent_ref = home.retain(
            {
                "invocation": dict(context),
                "expected_attempt": context["attempt"] + 1,
                "expected_node": _selected_role_node(
                    home, context, selection, "repair"
                ),
                "projection_ref": home.retain(projection),
                "basis_ref": home.retain(checked),
                "before_ref": before_ref,
                "snapshot_ref": snapshot_ref,
            }
        )
        operation = vr.validate_materialization_operation(
            {
                "format": vr.MATERIALIZATION_OPERATION,
                "operation_id": operation_id,
                "materialization_id": lease["materialization_id"],
                "operation": "repair",
                "request_sha256": vr.digest(projection),
                "before_tree_sha256": vr.tree_digest(before),
                "after_tree_sha256": None,
                "beforeimages_ref": before_ref,
                "allowed_writes_ref": home.retain(allowed_paths),
                "write_intent_ref": intent_ref,
                "completion_ref": None,
                "previous_receipt_ref": None,
                "status": "reserved",
                "restoration_ref": None,
            }
        )
        _save_lease(
            home,
            lease
            | {
                "state": "repair_reserved",
                "operation_refs": [*lease["operation_refs"], home.retain(operation)],
            },
            lease["version"],
        )
    consuming_workspace = Path(str(context["cwd"]))
    artifact_root, pointer = _publish_projection(home, consuming_workspace, pointer)
    publication = {
        "artifact_root": str(artifact_root),
        "pointer": pointer,
        "consuming_workspace": str(consuming_workspace),
        "projection_ref": home.retain(projection),
    }
    write_exact(
        home.workspace / (attempt_id + ".facade-publication.json"),
        canonical(publication),
    )
    return projection


def _classify(home, path: str) -> str:
    from . import check_contracts as cc

    return cc.classify(home.policy, path)


def project_candidate_role(
    home: V2Home,
    subject_ref: object,
    role: str,
    basis: object,
    controls: Path,
    invocation: Mapping[str, object],
) -> dict[str, object]:
    """Prepare one role's exact input/reservation; dispatch no worker or next node."""
    context = _facade_context(home, invocation, controls, "project-role")
    with home.lock(), home.state_lock():
        _prior_roles_closed(home)
        return _project_role_locked(home, subject_ref, role, basis, controls, context)


def _prepare_review_locked(
    home, request: Mapping[str, object], controls: Path, context: Mapping[str, object]
) -> dict[str, object]:
    import uuid

    from . import cicd_v2_records as vr

    candidate = _candidate(home, request["candidate_ref"])
    lease = _lease(home, str(request["materialization_id"]))
    if (
        lease is None
        or lease["version"] != request["expected_materialization_version"]
        or lease["state"] != "sealed"
        or lease["candidate_ref"] != request["candidate_ref"]
    ):
        raise CicdError("materialization_not_sealed")
    _require_accounted_operations(home, lease)
    reviewers = cast(list[dict[str, object]], home.config["role_bindings"]["review"])
    selections = [row for row in reviewers if row["id"] == request["role_binding_id"]]
    if (
        len(selections) != 1
        or request["role_binding_id"] not in home.authority["operations"]
    ):
        raise CicdError("review_binding_mismatch")
    if vr.tree_digest(_tree(home, home.target)) != candidate["candidate_tree_sha256"]:
        raise CicdError("candidate_tree_mismatch")
    selection = selections[0]
    attempt = uuid.uuid4().hex
    basis = vr.digest(
        {
            "candidate_ref": request["candidate_ref"],
            "lease": lease,
            "evidence_refs": request["evidence_refs"],
            "config_ref": home.config_ref,
        }
    )
    document = {
        "format": "millrace-plus-cicd-candidate-review-input/v1",
        "candidate_ref": request["candidate_ref"],
        "materialization_id": lease["materialization_id"],
        "attempt_id": attempt,
        "binding_id": selection["id"],
        "basis_sha256": basis,
        "checks_refs": request["evidence_refs"],
        "config_ref": home.config_ref,
        "output": attempt + ".result.json",
    }
    projection = vr.validate_review_projection(
        {
            "format": vr.REVIEW_PROJECTION,
            "candidate_id": candidate["candidate_id"],
            "materialization_id": lease["materialization_id"],
            "attempt_id": attempt,
            "binding_id": selection["id"],
            "input_ref": home.retain(document),
            "prompt_ref": selection["prompt_ref"],
            "skill_ref": selection["skill_ref"],
            "result_schema_ref": selection["result_schema_ref"],
            "basis_sha256": basis,
        }
    )
    write_exact(
        home.workspace / (attempt + ".candidate-review.json"),
        canonical(
            {
                "projection_ref": home.retain(projection),
                "preparer": dict(context),
                "expected_attempt": context["attempt"] + 1,
                "expected_node": _selected_role_node(
                    home, context, selection, "review"
                ),
                "candidate_ref": request["candidate_ref"],
                "snapshot_ref": home.retain(home.observe()),
            }
        ),
    )
    pointer = {
        "format": vr.ACTIVE_ROLE,
        "subject_id": candidate["candidate_id"],
        "role": "review",
        "attempt_id": attempt,
        "input_ref": projection["input_ref"],
        "projection_ref": home.retain(projection),
        "basis_sha256": basis,
    }
    artifact_root, pointer = _publish_projection(
        home, Path(str(context["cwd"])), pointer
    )
    write_exact(
        home.workspace / (attempt + ".review-publication.json"),
        canonical(
            {
                "artifact_root": str(artifact_root),
                "pointer": pointer,
                "consuming_workspace": str(context["cwd"]),
                "projection_ref": home.retain(projection),
            }
        ),
    )
    return projection


def prepare_candidate_review(
    home: V2Home, request: object, controls: Path, invocation: Mapping[str, object]
) -> dict[str, object]:
    """Reserve one selected review and project data; completed never means ACCEPT."""
    from . import cicd_v2_records as vr

    selected = vr.validate_candidate_request(request, operation="review")
    context = _facade_context(home, invocation, controls, "review")
    with home.lock(), home.state_lock():
        _prior_roles_closed(home)
        return _prepare_review_locked(home, selected, controls, context)


def _check_candidate(
    home, request: Mapping[str, object], controls: Path, context: Mapping[str, object]
) -> dict[str, object]:
    import time

    from . import cicd_v2_checks as checks
    from . import cicd_v2_records as vr
    from .cicd_v2_runtime import Runtime, _read

    candidate = _candidate(home, request["candidate_ref"])
    lease = _lease(home, str(request["materialization_id"]))
    if lease is None or lease["state"] not in {"base_ready", "sealed"}:
        raise CicdError("materialization_not_ready")
    _require_accounted_operations(home, lease)
    required = (
        lease["origin_base_candidate_ref"]
        if lease["state"] == "base_ready"
        else lease["candidate_ref"]
    )
    if request["candidate_ref"] != required:
        raise CicdError("check_candidate_mismatch")
    commands = [
        row for row in home.config["commands"] if row["id"] == request["command_id"]
    ]
    if len(commands) != 1 or request["command_id"] not in home.authority["operations"]:
        raise CicdError("command_not_authorized")
    command = commands[0]
    tree = _tree(home, home.target)
    if vr.tree_digest(tree) != candidate["candidate_tree_sha256"]:
        raise CicdError("candidate_tree_mismatch")
    prior_owned = set()
    earlier_images = {}
    for ref in lease["operation_refs"]:
        previous = home.read_ref(ref)
        if (
            previous["operation"] == "check_products"
            and previous["status"] == "applied"
        ):
            from .cicd_v2_runtime import _object

            accepted = _object(
                _read(
                    home.workspace / (previous["operation_id"] + ".check-accepted.json")
                )
            )
            layer = home.read_ref(accepted["product_ref"])
            prior_owned.update(layer["owned_paths"])
            products = home.read_ref(layer["after_ref"])["products"]
            earlier_images.update(
                {path: products[path] for path in layer["owned_paths"]}
            )
    before = home.observe()
    checks.product_preflight(home.policy, command, before["products"], earlier_images)
    environment_before = home.retain(Runtime(home, controls)._environment(before))
    lease, operation = _reserve_operation(
        home, lease, "check_products", request, tree, tree, context
    )
    before_ref = home.retain(before)
    images = {
        path: home.retain_bytes(_read(home.target / path))
        for path, entry in before["products"].items()
        if entry["kind"] == "file"
    }
    reserved = checks.product_layer(
        input_id=None,
        materialization_id=lease["materialization_id"],
        command=command,
        policy=home.policy,
        policy_ref=home.config["path_policy_ref"],
        before_ref=before_ref,
        after_ref=None,
        owned_paths=[],
        completion_ref=None,
        previous_ref=None,
        status="reserved",
    )
    reserved_ref = home.retain(reserved)
    write_exact(
        home.workspace / (operation["operation_id"] + ".check-intent.json"),
        canonical(
            {
                "operation_ref": home.retain(operation),
                "product_ref": reserved_ref,
                "beforeimages_ref": home.retain(images),
                "environment_before_ref": environment_before,
            }
        ),
    )
    with checks.TermPropagation() as term:
        observed = checks.run_command(
            home.target,
            home.policy,
            command,
            home.guards,
            base_environment=os.environ,
            deadline=min(context["deadline"], time.time() + command["timeout_seconds"]),
            cancelled=term.cancelled,
            on_started=term.started,
        )
    ownership = checks.product_ownership(observed, prior_owned)
    layer = checks.product_layer(
        input_id=None,
        materialization_id=lease["materialization_id"],
        command=command,
        policy=home.policy,
        policy_ref=home.config["path_policy_ref"],
        before_ref=before_ref,
        after_ref=home.retain(observed["after"]),
        owned_paths=ownership["owned_paths"],
        completion_ref=None,
        previous_ref=reserved_ref,
        status="observed",
    )
    layer_ref = home.retain(layer)
    reproduction = home.config["reproduction"]
    patterns = vr.validate_failure_patterns(
        home.read_ref(reproduction["failure_patterns_ref"])
    )
    matcher = reproduction["failure_matcher"]
    matched = (
        checks.matched_failures(
            observed["stdout"],
            observed["stderr"],
            matcher["evidence_pattern_ids"],
            patterns,
        )
        if command["id"] in reproduction["command_ids"]
        else []
    )
    receipt = vr.validate_check(
        {
            "format": vr.CHECK,
            "input_id": None,
            "candidate_id": candidate["candidate_id"],
            "materialization_id": lease["materialization_id"],
            "attempt_id": operation["operation_id"],
            "command_id": command["id"],
            **{
                key: observed[key]
                for key in (
                    "command_sha256",
                    "argv",
                    "cwd",
                    "environment_provenance",
                    "source_before_sha256",
                    "source_after_sha256",
                    "started_at",
                    "finished_at",
                    "exit_code",
                    "process_facts",
                    "diagnostic",
                )
            },
            "path_policy_ref": home.config["path_policy_ref"],
            "environment_before_ref": environment_before,
            "environment_after_ref": home.retain(
                Runtime(home, controls)._environment(observed["after"])
            ),
            "product_refs": [layer_ref],
            "head_sha": candidate["anchor_head"],
            "status": "failed"
            if matched and observed["status"] == "passed"
            else observed["status"],
            "stdout_ref": home.retain_bytes(observed["stdout"], ".stdout"),
            "stderr_ref": home.retain_bytes(observed["stderr"], ".stderr"),
            "matched_failure_ids": matched,
        }
    )
    raw_ref = home.retain(receipt)
    write_exact(
        home.workspace / (operation["operation_id"] + ".check-observed.json"),
        canonical(
            {"receipt_ref": raw_ref, "product_ref": layer_ref, "ownership": ownership}
        ),
    )
    result = vr.validate_candidate_result(
        {
            "format": vr.CANDIDATE_RESULT,
            "request_id": request["request_id"],
            "request_sha256": vr.digest(request),
            "operation": "check",
            "candidate_id": candidate["candidate_id"],
            "materialization_id": lease["materialization_id"],
            "materialization_version": lease["version"],
            "status": "completed",
            "target_before_sha256": source_identity(before),
            "target_after_sha256": source_identity(observed["after"]),
            "artifact_refs": [raw_ref, home.retain(operation)],
            "writer_facts": {
                "core_invocation_id": context["invocation_id"],
                "writer_state": "may_live",
            },
            "refusal": None,
        }
    )
    write_exact(
        home.workspace / (vr.digest(request["request_id"]) + ".candidate-result.json"),
        canonical(result),
    )
    return result


def _verify_candidate(
    home, request: Mapping[str, object], controls: Path, context: Mapping[str, object]
) -> dict[str, object]:
    from . import check_contracts as cc
    from . import cicd_v2_records as vr

    candidate = _candidate(home, request["candidate_ref"])
    lease = _lease(home, str(request["materialization_id"]))
    if (
        lease is None
        or lease["state"] != "sealed"
        or lease["candidate_ref"] != request["candidate_ref"]
    ):
        raise CicdError("materialization_not_sealed")
    _require_accounted_operations(home, lease)
    actual = _tree(home, home.target)
    if vr.tree_digest(actual) != candidate["candidate_tree_sha256"]:
        raise CicdError("candidate_tree_mismatch")
    accepted_checks, reviews = [], []
    review_documents = []
    by_id = {}
    by_command = {row["id"]: row for row in home.config["commands"]}
    accepted_by_command = {}
    selected_reviewers = set()
    for ref in request["evidence_refs"]:
        value = home.read_ref(ref)
        if value.get("format") == cc.COMPLETION_ACCEPTANCE:
            acceptance = cc.validate_completion_acceptance(
                value, "public_core_completion"
            )
            check = vr.validate_check(home.read_ref(acceptance["process_receipt_ref"]))
            if (
                check["candidate_id"] != candidate["candidate_id"]
                or check["materialization_id"] != lease["materialization_id"]
                or check["status"] != "passed"
                or check["matched_failure_ids"]
                or check["source_before_sha256"] != source_identity(home.observe())
                or check["source_after_sha256"] != source_identity(home.observe())
            ):
                raise CicdError("candidate_checks_unproven")
            invocation = home.read_ref(acceptance["acceptance"]["invocation_ref"])
            matching = [
                home.read_ref(item)
                for item in lease["operation_refs"]
                if home.read_ref(item)["operation_id"] == check["attempt_id"]
                and home.read_ref(item)["status"] == "applied"
            ]
            if len(matching) != 1:
                raise CicdError("check_operation_unaccounted")
            intent = home.read_ref(matching[0]["write_intent_ref"])
            _, completion = _completed(
                home,
                intent["invocation"],
                acceptance["acceptance"]["completion_ref"],
                expected_invocation=str(invocation["id"]),
            )
            command = by_command.get(check["command_id"])
            if (
                completion.get("transport") != "completed"
                or completion.get("classification") != "semantic"
                or check["command_id"] in by_id
                or command is None
                or check["command_sha256"]
                != cc.execution_sha256(cc.execution_of(command, policy=home.policy))
            ):
                raise CicdError("candidate_checks_unproven")
            accepted_by_command[check["command_id"]] = ref
            by_id[check["command_id"]] = check
            accepted_checks.append(ref)
        elif value.get("format") == vr.REVIEW:
            review = vr.validate_review(value)
            if (
                review["candidate_id"] != candidate["candidate_id"]
                or review["materialization_id"] != lease["materialization_id"]
                or review["verdict"] != "ACCEPT"
                or review["candidate_sha256"] != source_identity(home.observe())
            ):
                raise CicdError("candidate_review_unproven")
            preparation_path = home.workspace / (
                str(review["attempt_id"]) + ".candidate-review.json"
            )
            from .cicd_v2_runtime import _object, _read

            preparation = _object(_read(preparation_path))
            projection = vr.validate_review_projection(
                home.read_ref(preparation["projection_ref"])
            )
            proof_refs = [
                home.read_ref(item)
                for item in request["evidence_refs"]
                if home.read_ref(item).get("attempt_id") == review["attempt_id"]
                and home.read_ref(item).get("completion_ref") is not None
            ]
            if len(proof_refs) != 1:
                raise CicdError("review_completion_missing")
            accepted = _object(
                _read(
                    home.workspace
                    / (str(review["attempt_id"]) + ".candidate-review-accepted.json")
                )
            )
            if (
                accepted != proof_refs[0]
                or accepted["review_ref"] != ref
                or accepted["projection_ref"] != preparation["projection_ref"]
            ):
                raise CicdError("review_acceptance_mismatch")
            _, done = _completed(
                home,
                preparation["preparer"],
                proof_refs[0]["completion_ref"],
                expected_attempt=preparation["expected_attempt"],
                expected_node=preparation["expected_node"],
            )
            if (
                done.get("outcome") != "ACCEPT"
                or done.get("classification") != "semantic"
            ):
                raise CicdError("candidate_review_unproven")
            selected_reviewers.add(projection["binding_id"])
            reviews.append(ref)
            review_documents.append(review)
    delta = vr.validate_delta(home.read_ref(candidate["delta_ref"]))
    plan = vr.select_checks(
        home.config, [item["path"] for item in delta["changes"]], uncertain=False
    )
    if set(by_id) != set(plan["command_ids"]):
        raise CicdError("candidate_check_set_incomplete")
    if selected_reviewers != {
        row["id"] for row in home.config["role_bindings"]["review"]
    } or len(reviews) != len(selected_reviewers):
        raise CicdError("candidate_review_set_incomplete")
    exact_checks = [
        accepted_by_command[identifier] for identifier in plan["command_ids"]
    ]
    if any(review["checks_refs"] != exact_checks for review in review_documents):
        raise CicdError("candidate_review_checks_mismatch")
    return vr.validate_candidate_verification(
        {
            "format": vr.CANDIDATE_VERIFICATION,
            "candidate_id": candidate["candidate_id"],
            "materialization_id": lease["materialization_id"],
            "config_sha256": home.config_sha256,
            "observed_head": actual["anchor_head"],
            "observed_source_sha256": source_identity(home.observe()),
            "checks_refs": accepted_checks,
            "review_refs": reviews,
            "status": "verified",
            "evidence_refs": list(request["evidence_refs"]),
            "refusal": None,
        }
    )


def _validate_repair_delta(
    home,
    operation: Mapping[str, object],
    intent: Mapping[str, object],
    actual: dict[str, object],
    completion: Mapping[str, object],
) -> None:
    from . import cicd_v2_records as vr
    from .cicd_v2_runtime import _object, _read

    if (
        completion.get("transport") != "completed"
        or completion.get("classification") != "semantic"
        or completion.get("outcome") != "PATCH_READY"
    ):
        raise CicdError("repair_completion_unproven")
    before = vr.validate_tree(home.read_ref(intent["before_ref"]))
    delta = tree_delta(before, actual)
    # Allowed writes is a JSON array, so resolve it without object coercion.
    from .local_intake import strict_json

    allowed = strict_json(_read(home.resolve_ref(operation["allowed_writes_ref"])))
    if not isinstance(allowed, list):
        raise CicdError("repair_scope_mismatch")
    basis = vr.validate_role_basis(home.read_ref(intent["basis_ref"]), role="repair")
    assessment = vr.validate_assessment(home.read_ref(basis["assessment_ref"]))
    projection = vr.validate_subject_projection(home.read_ref(intent["projection_ref"]))
    role_input = home.read_ref(projection["input_ref"])
    publication = _projection_publication(home, projection, "facade")
    repair = vr.validate_repair(
        _object(_read(Path(publication["artifact_root"]) / str(role_input["output"])))
    )
    paths = [str(row["path"]) for row in delta["changes"]]
    if (
        repair["input_id"] != projection["subject_id"]
        or repair["attempt_id"] != projection["attempt_id"]
        or repair["assessment_ref"] != basis["assessment_ref"]
        or sorted(repair["changed_paths"]) != paths
    ):
        raise CicdError("repair_basis_mismatch")
    for path in paths:
        if (
            vr.structural_forbidden(path)
            or _classify(home, path) != "source"
            or not any(
                path == prefix or path.startswith(prefix + "/") for prefix in allowed
            )
            or not any(
                path == prefix or path.startswith(prefix + "/")
                for prefix in home.authority["repair_paths"]
            )
            or any(
                path == prefix or path.startswith(prefix + "/")
                for prefix in home.config["protected_paths"]
            )
        ):
            raise CicdError("repair_scope_forbidden")
    binary = [
        row["path"]
        for row in delta["changes"]
        if row["after"] is not None
        and b"\0" in _read(home.resolve_ref(row["after"]["payload_ref"]))
    ]
    detected = vr.detect_change_classes(
        {row["path"]: (row["before"], row["after"]) for row in delta["changes"]},
        binary_paths=binary,
    )
    classes = vr.effective_change_classes(
        {kind for kinds in detected.values() for kind in kinds},
        assessment["change_classes"],
    )
    classes = vr.effective_change_classes(classes, repair["change_classes"])
    protected = set(home.config["protected_change_classes"])
    permitted = set(home.config["repair_policy"]["allowed_change_classes"])
    for kind in classes:
        if kind in protected:
            if not all(
                any(
                    item["class"] == kind
                    and any(
                        path == prefix or path.startswith(prefix + "/")
                        for prefix in item["paths"]
                    )
                    for item in home.config["class_exceptions"]
                )
                for path in paths
            ):
                raise CicdError("protected_change_class")
        elif kind not in permitted:
            raise CicdError("change_class_not_allowed")
    _selected_repair_caps(home, delta)
    if home.observe()["products"] != home.read_ref(intent["snapshot_ref"])["products"]:
        raise CicdError("noncheck_product_write")
    if repair["source_before_sha256"] != basis["source_before_sha256"] or repair[
        "source_after_sha256"
    ] != source_identity(home.observe()):
        raise CicdError("repair_source_mismatch")
    home.resolve_ref(repair["patch_ref"])


def _accept_candidate_check(
    home,
    operation: Mapping[str, object],
    invocation: Mapping[str, object],
    completion: Mapping[str, object],
    completion_ref: object,
    guard: Mapping[str, object],
) -> dict[str, object]:
    from . import check_contracts as cc
    from . import cicd_v2_checks as checks
    from . import cicd_v2_records as vr
    from .cicd_v2_runtime import _object, _read

    observed_path = home.workspace / (
        str(operation["operation_id"]) + ".check-observed.json"
    )
    observed = _object(_read(observed_path))
    receipt = vr.validate_check(home.read_ref(observed["receipt_ref"]))
    facts = receipt["process_facts"]
    if (
        not all(facts[k] for k in ("root_reaped", "stdout_eof", "stderr_eof"))
        or facts["group_state"] != "closed"
    ):
        raise CicdError("check_writer_unknown")
    if observed["ownership"]["hold"] is not None:
        raise CicdError(str(observed["ownership"]["hold"]))
    layer = cc.validate_products(home.read_ref(observed["product_ref"]))
    if home.observe()["products"] != home.read_ref(layer["after_ref"])["products"]:
        raise CicdError("product_drift")
    intent = home.read_ref(operation["write_intent_ref"])
    request = intent["request"]
    acceptance = checks.completion_acceptance(
        subject_ref=request["candidate_ref"],
        command_id=str(receipt["command_id"]),
        process_receipt_ref=observed["receipt_ref"],
        invocation_ref=home.retain(invocation),
        completion_ref=completion_ref,
        guard_ref=home.retain(guard),
    )
    acceptance_ref = home.retain(acceptance)
    accepted = layer | {
        "status": "accepted",
        "completion_ref": acceptance_ref,
        "previous_ref": observed["product_ref"],
    }
    cc.validate_product_transition(
        _read(home.resolve_ref(observed["product_ref"])),
        observed["product_ref"],
        accepted,
    )
    record = {"acceptance_ref": acceptance_ref, "product_ref": home.retain(accepted)}
    write_exact(
        home.workspace / (str(operation["operation_id"]) + ".check-accepted.json"),
        canonical(record),
    )
    return record


def _restore_candidate(
    home,
    request: Mapping[str, object],
    context: Mapping[str, object],
    lease: dict[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    from . import cicd_v2_checks as checks
    from . import cicd_v2_records as vr
    from .cicd_v2_runtime import _object, _read

    operations = {
        str(home.read_ref(ref)["operation_id"]): home.read_ref(ref)
        for ref in lease["operation_refs"]
    }
    if not operations:
        raise CicdError("operation_missing")
    simulated = _tree(home, home.target)
    products = dict(home.observe()["products"])
    plans, never_started_refs = [], []
    for operation in reversed(list(operations.values())):
        if operation["status"] != "applied":
            if operation["operation"] != "repair" or operation["status"] != "reserved":
                raise CicdError("writer_or_layer_unknown")
            intent = home.read_ref(operation["write_intent_ref"])
            projection = vr.validate_subject_projection(
                home.read_ref(intent["projection_ref"])
            )
            preparation_path = home.workspace / (
                projection["attempt_id"] + ".facade-role.json"
            )
            preparation = _object(_read(preparation_path))
            if (
                preparation["projection_ref"] != intent["projection_ref"]
                or preparation["preparer"] != intent["invocation"]
                or preparation["expected_attempt"] != intent["expected_attempt"]
                or preparation["expected_node"] != intent["expected_node"]
                or operation["request_sha256"] != vr.digest(projection)
            ):
                raise CicdError("repair_preparation_changed")
            proof = _never_started_role(home, preparation_path, preparation)
            before = vr.validate_tree(home.read_ref(intent["before_ref"]))
            if (
                simulated != before
                or products != home.read_ref(intent["snapshot_ref"])["products"]
            ):
                raise CicdError("materialization_drift")
            never_started_refs.append(proof)
            continue
        intent = home.read_ref(operation["write_intent_ref"])
        producer = intent["invocation"]
        _completed(
            home,
            producer,
            operation["completion_ref"],
            expected_invocation=None
            if operation["operation"] == "repair"
            else producer["invocation_id"],
            expected_attempt=intent.get("expected_attempt")
            if operation["operation"] == "repair"
            else None,
            expected_node=intent.get("expected_node")
            if operation["operation"] == "repair"
            else None,
        )
        before = vr.validate_tree(home.read_ref(intent["before_ref"]))
        after_ref = {
            "path": str(operation["after_tree_sha256"]) + ".json",
            "sha256": operation["after_tree_sha256"],
        }
        after = vr.validate_tree(home.read_ref(after_ref))
        delta = tree_delta(before, after)
        if simulated["baseline_links"] != after["baseline_links"]:
            raise CicdError("baseline_links_changed")
        entries = {row["path"]: row for row in simulated["entries"]}
        for change in delta["changes"]:
            if entries.get(change["path"]) != change["after"]:
                raise CicdError("materialization_drift")
            if change["before"] is None:
                entries.pop(change["path"], None)
            else:
                entries[change["path"]] = change["before"]
        simulated = simulated | {"entries": [entries[path] for path in sorted(entries)]}
        product_plan = None
        if operation["operation"] == "check_products":
            accepted = _object(
                _read(
                    home.workspace
                    / (operation["operation_id"] + ".check-accepted.json")
                )
            )
            layer = home.read_ref(accepted["product_ref"])
            old = home.read_ref(layer["before_ref"])["products"]
            new = home.read_ref(layer["after_ref"])["products"]
            check_intent = _object(
                _read(
                    home.workspace / (operation["operation_id"] + ".check-intent.json")
                )
            )
            images = {
                path: _read(home.resolve_ref(ref))
                for path, ref in home.read_ref(check_intent["beforeimages_ref"]).items()
            }
            for path in layer["owned_paths"]:
                if products.get(path) != new.get(path):
                    raise CicdError("product_drift")
                if path in old:
                    products[path] = old[path]
                else:
                    products.pop(path, None)
            product_plan = (layer, old, new, images)
        plans.append((delta, product_plan))
    first = next(iter(operations.values()))
    origin = vr.validate_tree(home.read_ref(first["beforeimages_ref"]))
    if simulated != origin:
        raise CicdError("foreign_materialization_drift")
    current = _tree(home, home.target)
    lease, restore = _reserve_operation(
        home, lease, "restore", request, current, origin, context
    )
    write_exact(
        home.workspace / (restore["operation_id"] + ".restore-plan.json"),
        canonical(
            {
                "operation_refs": list(lease["operation_refs"]),
                "never_started_refs": never_started_refs,
                "products_after": products,
                "source_after_tree_ref": home.retain(origin),
            }
        ),
    )
    for delta, product_plan in plans:
        if product_plan is not None:
            layer, old, new, images = product_plan
            result = checks.reverse_products(
                home.target,
                layer["owned_paths"],
                new,
                old,
                home.observe()["products"],
                images,
            )
            if result["status"] != "reversed":
                raise CicdError(str(result["reason"]))
        apply_delta(home.target, delta, home.workspace, inverse=True)
    return lease, restore


def _active_guard(
    home, producer: Mapping[str, object], operation: str
) -> dict[str, object]:
    """The following metadata guard must itself be an actual captured command."""
    from .router import control

    view = control.inspect_run(
        str(producer["instance_id"]),
        str(producer["run_id"]),
        state_home=Path(str(producer["state_home"])),
    )
    rows = [
        row
        for row in view["invocations"]
        if row.get("status") == "active"
        and row.get("pid") == os.getpid()
        and row.get("plane_id") == producer["plane_id"]
    ]
    if len(rows) != 1 or rows[0]["id"] == producer["invocation_id"]:
        raise CicdError("following_guard_not_active")
    actor_homes = [
        Path(str(actor["execution_home"]))
        for actor in home.authority["actors"]
        if actor["instance_id"] in {None, producer["instance_id"]}
    ]
    matching = []
    from .cicd_v2_runtime import _object, _read

    for execution_home in actor_homes:
        descriptor = _object(_read(execution_home / "execution.json"))
        if descriptor["instance_id"] == producer["instance_id"]:
            matching.append(execution_home)
    if len(matching) != 1:
        raise CicdError("actor_binding_mismatch")
    scripts = [
        Path(arg)
        for arg in rows[0]["argv"]
        if arg.endswith(".py") and "/controls/" in arg
    ]
    if len(scripts) != 1:
        raise CicdError("invocation_argv_mismatch")
    script = scripts[0]
    context = bind_invocation(
        matching[0],
        str(producer["plane_id"]),
        str(rows[0]["node"]),
        script,
        os.getpid(),
        Path.cwd(),
    )
    return _facade_context(home, context, script.parent.parent, operation)


def _require_accounted_operations(home, lease: Mapping[str, object]) -> None:
    operations = {
        home.read_ref(ref)["operation_id"]: home.read_ref(ref)
        for ref in lease["operation_refs"]
    }
    if any(operation["status"] != "applied" for operation in operations.values()):
        raise CicdError("prior_operation_unaccounted")


def _selected_repair_caps(home, delta: Mapping[str, object]) -> None:
    """Apply only explicitly captured operational caps to the exact delta."""
    import difflib

    from .cicd_v2_runtime import _read

    for cap in home.config["repair_policy"]["optional_caps"]:
        if cap["kind"] == "files" and len(delta["changes"]) > cap["value"]:
            raise CicdError("selected_file_cap")
        if cap["kind"] == "cost":
            raise CicdError("spend_measurement_unavailable")
        if cap["kind"] == "changed_lines":
            count = 0
            for change in delta["changes"]:
                texts = []
                for key in ("before", "after"):
                    entry = change[key]
                    raw = (
                        b""
                        if entry is None
                        else _read(home.resolve_ref(entry["payload_ref"]))
                    )
                    texts.append(raw.decode("utf-8", "replace").splitlines())
                count += sum(line[:2] in {"- ", "+ "} for line in difflib.ndiff(*texts))
            if count > cap["value"]:
                raise CicdError("selected_changed_line_cap")


def _candidate_scope(home, candidate: Mapping[str, object]) -> None:
    from . import cicd_v2_records as vr

    delta = vr.validate_delta(home.read_ref(candidate["delta_ref"]))
    if not delta["changes"]:
        return
    _selected_repair_caps(home, delta)
    assessment = vr.validate_assessment(home.read_ref(candidate["assessment_ref"]))
    if assessment["route"] != "REPAIR_ALLOWED":
        raise CicdError("candidate_assessment_not_repairable")
    paths = [change["path"] for change in delta["changes"]]
    for path in paths:
        if (
            vr.structural_forbidden(path)
            or _classify(home, path) != "source"
            or not any(
                path == prefix or path.startswith(prefix + "/")
                for prefix in home.config["repair_paths"]
            )
            or not any(
                path == prefix or path.startswith(prefix + "/")
                for prefix in home.authority["repair_paths"]
            )
            or not any(
                path == prefix or path.startswith(prefix + "/")
                for prefix in assessment["proposed_paths"]
            )
            or any(
                path == prefix or path.startswith(prefix + "/")
                for prefix in home.config["protected_paths"]
            )
        ):
            raise CicdError("candidate_scope_forbidden")
    binary = [
        change["path"]
        for change in delta["changes"]
        if change["after"] is not None
        and b"\0" in home.resolve_ref(change["after"]["payload_ref"]).read_bytes()
    ]
    detected = vr.detect_change_classes(
        {
            change["path"]: (change["before"], change["after"])
            for change in delta["changes"]
        },
        binary_paths=binary,
    )
    classes = vr.effective_change_classes(
        {kind for values in detected.values() for kind in values},
        assessment["change_classes"],
    )
    for kind in classes:
        if kind in home.config["protected_change_classes"]:
            if not all(
                any(
                    item["class"] == kind
                    and any(
                        path == prefix or path.startswith(prefix + "/")
                        for prefix in item["paths"]
                    )
                    for item in home.config["class_exceptions"]
                )
                for path in paths
            ):
                raise CicdError("protected_change_class")
        elif kind not in home.config["repair_policy"]["allowed_change_classes"]:
            raise CicdError("change_class_not_allowed")


def _leased_export_origin(
    home,
    request: Mapping[str, object],
    basis: Mapping[str, object],
    before: Mapping[str, object],
    observed: Mapping[str, object],
) -> list[object]:
    """A8: explicit native-repair evidence selects physical validation-root export."""
    from . import cicd_v2_records as vr

    lease = _lease(home, request["materialization_id"])
    if (
        request["materialization_id"] is None
        or lease is None
        or lease["version"] != request["expected_materialization_version"]
        or lease["owner_ref"] != home.record["owner_ref"]
        or lease["candidate_ref"] is not None
        or lease["state"] != "repair_reserved"
        or lease["origin_base_candidate_ref"] != basis["prior_candidate_ref"]
    ):
        raise CicdError("leased_export_unproven")
    predecessor = _candidate(home, basis["prior_candidate_ref"])
    if (
        basis["source_target"] != predecessor["source_target"]
        or basis["verification_target"] != predecessor["verification_target"]
        or before["target"] != predecessor["verification_target"]
        or basis["logical_base_id"] != predecessor["candidate_tree_sha256"]
    ):
        raise CicdError("leased_export_origin_mismatch")
    _require_accounted_operations(home, lease)
    current = {
        home.read_ref(ref)["operation_id"]: home.read_ref(ref)
        for ref in lease["operation_refs"]
    }
    repairs = [
        operation
        for operation in current.values()
        if operation["operation"] == "repair"
    ]
    if len(repairs) != 1:
        raise CicdError("leased_repair_ambiguous")
    operation = repairs[0]
    intent = home.read_ref(operation["write_intent_ref"])
    projection = vr.validate_subject_projection(home.read_ref(intent["projection_ref"]))
    role_basis = vr.validate_role_basis(
        home.read_ref(intent["basis_ref"]), role="repair"
    )
    if (
        projection["subject_ref"] != request["subject_ref"]
        or role_basis["assessment_ref"] != basis["assessment_ref"]
    ):
        raise CicdError("leased_repair_subject_mismatch")
    reserved_before = vr.validate_tree(home.read_ref(operation["beforeimages_ref"]))
    initial = {
        "format": vr.TREE,
        "repository": home.config["repository"],
        "anchor_head": before["anchor_head"],
        "path_policy_ref": before["path_policy_ref"],
        "entries": before["entries"],
        "baseline_links": before["baseline_links"],
    }
    if (
        initial != reserved_before
        or before["source_sha256"] != role_basis["source_before_sha256"]
    ):
        raise CicdError("leased_repair_before_mismatch")
    invocation, done = _completed(
        home,
        intent["invocation"],
        operation["completion_ref"],
        expected_attempt=intent["expected_attempt"],
        expected_node=intent["expected_node"],
    )
    if invocation["id"] != basis["writer_facts"]["core_invocation_id"] or operation[
        "after_tree_sha256"
    ] != vr.tree_digest(dict(observed)):
        raise CicdError("leased_repair_producer_mismatch")
    _validate_repair_delta(home, operation, intent, dict(observed), done)
    return [
        home.retain(lease),
        *lease["operation_refs"],
        intent["projection_ref"],
        intent["basis_ref"],
    ]


def _selected_role_node(
    home, context: Mapping[str, object], selection: Mapping[str, object], role: str
) -> str:
    from .cicd_v2_runtime import _object, _read

    definitions = []
    for actor in home.authority["actors"]:
        descriptor = _object(_read(Path(actor["execution_home"]) / "execution.json"))
        if descriptor["instance_id"] == context["instance_id"]:
            definitions.append(home.read_ref(actor["definition_ref"]))
    if len(definitions) != 1:
        raise CicdError("actor_definition_ambiguous")
    definition = definitions[0]
    aliases = {role, str(selection["id"])}
    if role == "review":
        aliases |= {
            f"review_{index}"
            for index, item in enumerate(home.config["role_bindings"]["review"], 1)
            if item["id"] == selection["id"]
        }
    expected_binding = dict(selection["binding"])
    if expected_binding["adapter"] != "command":
        expected_binding["config"] = expected_binding["config"] | {
            "profile": selection["profile_ref"]["path"]
        }
    nodes = definition["planes"][context["plane_id"]]["nodes"]
    selected = [
        name
        for name, node in nodes.items()
        if node["invoke"]["binding"] in aliases
        and definition["bindings"][node["invoke"]["binding"]] == expected_binding
    ]
    if len(selected) != 1:
        raise CicdError("selected_role_node_ambiguous")
    return selected[0]


def _accepted_role_assessment(
    home, basis: Mapping[str, object], subject_ref: object
) -> None:
    """A CI repair consumes an actual following-guard assessment acceptance."""
    from . import cicd_v2_records as vr
    from .cicd_v2_runtime import _object, _read

    accepted = []
    for ref in basis["public_writer_refs"]:
        value = home.read_ref(ref)
        if value.get("format") != vr.ATTEMPT:
            continue
        attempt = vr.validate_attempt(value)
        if (
            attempt["kind"] == "investigate"
            and attempt["status"] == "completed"
            and attempt["subject_ref"] == subject_ref
            and attempt["artifact_ref"] == basis["assessment_ref"]
        ):
            marker = _object(
                _read(
                    home.workspace
                    / (str(attempt["attempt_id"]) + ".facade-accepted.json")
                )
            )
            if marker != ref:
                raise CicdError("assessment_acceptance_mismatch")
            preparation = _object(
                _read(
                    home.workspace / (str(attempt["attempt_id"]) + ".facade-role.json")
                )
            )
            _, done = _completed(
                home,
                preparation["preparer"],
                attempt["completion_ref"],
                expected_attempt=preparation["expected_attempt"],
                expected_node=preparation["expected_node"],
            )
            if (
                done.get("classification") != "semantic"
                or done.get("transport") != "completed"
                or done.get("outcome") != "ASSESSED"
            ):
                raise CicdError("assessment_completion_unproven")
            accepted.append(ref)
    if len(accepted) != 1:
        raise CicdError("assessment_acceptance_missing")


def _reproduction_for_role(
    home,
    lease: Mapping[str, object],
    basis: Mapping[str, object],
    assessment: Mapping[str, object],
) -> None:
    from . import check_contracts as cc
    from . import cicd_v2_records as vr
    from .cicd_v2_runtime import _object, _read

    if assessment["reproduction_refs"] != basis["reproduction_refs"]:
        raise CicdError("reproduction_basis_mismatch")
    matcher = home.config["reproduction"]["failure_matcher"]
    commands = {command["id"]: command for command in home.config["commands"]}
    seen = set()
    for ref in basis["reproduction_refs"]:
        acceptance = cc.validate_completion_acceptance(
            home.read_ref(ref), "public_core_completion"
        )
        receipt = vr.validate_check(home.read_ref(acceptance["process_receipt_ref"]))
        command_id = receipt["command_id"]
        if (
            command_id in seen
            or command_id not in home.config["reproduction"]["command_ids"]
        ):
            raise CicdError("reproduction_command_mismatch")
        seen.add(command_id)
        if (
            receipt["materialization_id"] != lease["materialization_id"]
            or receipt["source_before_sha256"] != basis["source_before_sha256"]
            or receipt["source_after_sha256"] != basis["source_before_sha256"]
            or receipt["status"] != "failed"
            or receipt["exit_code"] not in matcher["failing_exit_codes"]
            or set(receipt["matched_failure_ids"])
            != set(matcher["evidence_pattern_ids"])
            or receipt["command_sha256"]
            != cc.execution_sha256(
                cc.execution_of(commands[command_id], policy=home.policy)
            )
        ):
            raise CicdError("reproduction_not_proven")
        operations = [
            home.read_ref(item)
            for item in lease["operation_refs"]
            if home.read_ref(item)["operation_id"] == receipt["attempt_id"]
            and home.read_ref(item)["status"] == "applied"
        ]
        if len(operations) != 1:
            raise CicdError("reproduction_operation_unaccounted")
        intent = home.read_ref(operations[0]["write_intent_ref"])
        accepted = _object(
            _read(home.workspace / (receipt["attempt_id"] + ".check-accepted.json"))
        )
        if accepted["acceptance_ref"] != ref:
            raise CicdError("reproduction_acceptance_mismatch")
        _completed(
            home,
            intent["invocation"],
            acceptance["acceptance"]["completion_ref"],
            expected_invocation=intent["invocation"]["invocation_id"],
        )
    if seen != set(home.config["reproduction"]["command_ids"]):
        raise CicdError("reproduction_set_incomplete")


def accept_candidate_role(
    home: V2Home, projection_ref: object, completion_ref: object
) -> dict[str, object]:
    """Metadata-only following guard for a projected read-only investigation.

    Repair acceptance uses its existing materialization operation instead.
    Neither helper dispatches a role or supplies its own closure evidence.
    """
    from . import cicd_v2_records as vr
    from .cicd_v2_runtime import _object, _read

    projection = vr.validate_subject_projection(home.read_ref(projection_ref))
    if projection["role"] != "investigate":
        raise CicdError("repair_requires_materialization_acceptance")
    path = home.workspace / (str(projection["attempt_id"]) + ".facade-role.json")
    preparation = _object(_read(path))
    if preparation["projection_ref"] != projection_ref:
        raise CicdError("role_projection_changed")
    with home.lock(), home.state_lock():
        guard = _active_guard(home, preparation["preparer"], "project-role")
        receipt_path = home.workspace / (
            str(projection["attempt_id"]) + ".facade-accepted.json"
        )
        if receipt_path.exists():
            finished = vr.validate_attempt(home.read_ref(_object(_read(receipt_path))))
            if finished["completion_ref"] != completion_ref:
                raise CicdError("role_completion_changed")
            return finished
        invocation, done = _completed(
            home,
            preparation["preparer"],
            completion_ref,
            expected_attempt=preparation["expected_attempt"],
            expected_node=preparation["expected_node"],
        )
        if (
            done.get("transport") != "completed"
            or done.get("classification") != "semantic"
            or done.get("outcome") != "ASSESSED"
        ):
            raise CicdError("assessment_completion_unproven")
        before = home.read_ref(preparation["snapshot_ref"])
        actual = home.observe()
        if (
            source_identity(before) != source_identity(actual)
            or before["products"] != actual["products"]
        ):
            raise CicdError("readonly_role_changed_target")
        document = home.read_ref(projection["input_ref"])
        publication = _projection_publication(home, projection, "facade")
        assessment = vr.validate_assessment(
            _object(_read(Path(publication["artifact_root"]) / document["output"]))
        )
        if (
            assessment["input_id"] != projection["subject_id"]
            or assessment["attempt_id"] != projection["attempt_id"]
            or assessment["subject_ref"] != projection["subject_ref"]
            or assessment["reproduction_refs"] != document["reproduction_refs"]
        ):
            raise CicdError("assessment_basis_mismatch")
        for ref in assessment["evidence_refs"]:
            home.resolve_ref(ref)
        attempt = vr.validate_attempt(home.read_ref(preparation["attempt_ref"]))
        finished = vr.validate_attempt(
            attempt
            | {
                "status": "completed",
                "artifact_ref": home.retain(assessment),
                "completion_ref": completion_ref,
                "reservation": attempt["reservation"]
                | {
                    "core_invocation_id": invocation["id"],
                    "core_attempt_id": invocation["attempt"],
                },
            }
        )
        write_exact(receipt_path, canonical(home.retain(finished)))
        home.retain(guard)
        return finished


def _never_started_role(home, path, preparation):
    """Positive terminal-parent history proof, never a native completion."""
    from .router import control

    producer = preparation["preparer"]
    view = control.inspect_run(
        producer["instance_id"],
        producer["run_id"],
        state_home=Path(producer["state_home"]),
    )
    rows = view["invocations"]
    done = view["completions"]
    prior = [row for row in rows if row["id"] == producer["invocation_id"]]
    ids = [row["id"] for row in rows]
    completion_ids = [row["invocation_id"] for row in done]
    if (
        view["status"] not in {"succeeded", "failed", "cancelled"}
        or not view["quiescent"]
        or len(prior) != 1
        or len(ids) != len(set(ids))
        or len(completion_ids) != len(set(completion_ids))
        or set(ids) != set(completion_ids)
        or any(row.get("writer_state") != "stopped" for row in rows)
        or any(row.get("effective_writer") != "stopped" for row in done)
        or any(
            prior[0].get(key) != producer[key]
            for key in (
                "plane_id",
                "node",
                "attempt",
                "pid",
                "cwd",
                "argv",
                "deadline",
                "revision_id",
            )
        )
        or any(
            row["plane_id"] == producer["plane_id"]
            and row["attempt"] > producer["attempt"]
            and row["node"] == preparation["expected_node"]
            for row in rows
        )
    ):
        raise CicdError("prior_role_writer_unknown")
    from .cicd_v2_runtime import _object, _read

    marker = path.with_suffix(".never-started.json")
    preparation_ref = home.file_ref(path)
    if marker.exists():
        proof = _object(_read(marker))
        saved = home.read_ref(proof)
        observed = home.read_ref(saved["public_view_ref"])
        if (
            saved["kind"] != "projection_never_started"
            or saved["preparation_ref"] != preparation_ref
            or any(
                observed[key] != view[key]
                for key in ("id", "status", "quiescent", "invocations", "completions")
            )
        ):
            raise CicdError("never_started_proof_changed")
        return proof
    proof = home.retain(
        {
            "kind": "projection_never_started",
            "preparation_ref": preparation_ref,
            "public_view_ref": home.retain(view),
        }
    )
    write_exact(marker, canonical(proof), "never_started_proof_changed")
    return proof


def _prior_roles_closed(home) -> None:
    from .cicd_v2_runtime import _object, _read
    from .router import control

    for path in sorted(
        [
            *home.workspace.glob("*.facade-role.json"),
            *home.workspace.glob("*.candidate-review.json"),
        ]
    ):
        preparation = _object(_read(path))
        producer = preparation["preparer"]
        view = control.inspect_run(
            producer["instance_id"],
            producer["run_id"],
            state_home=Path(producer["state_home"]),
        )
        rows = [
            row
            for row in view["invocations"]
            if row["plane_id"] == producer["plane_id"]
            and row["attempt"] == preparation["expected_attempt"]
            and row["node"] == preparation["expected_node"]
        ]
        if not rows:
            _never_started_role(home, path, preparation)
            continue
        if len(rows) != 1 or rows[0].get("writer_state") != "stopped":
            raise CicdError("prior_role_writer_unknown")
        done = [
            row for row in view["completions"] if row["invocation_id"] == rows[0]["id"]
        ]
        if len(done) != 1 or done[0].get("effective_writer") != "stopped":
            raise CicdError("prior_role_writer_unknown")


def _publish_projection(
    home, consuming_workspace: Path, pointer: dict[str, object]
) -> tuple[Path, dict[str, object]]:
    """Copy exact referenced records into this consuming actor's scoped directory.

    Original record bytes and their internal relative refs remain unchanged.
    Only the outer active pointer names the scoped directory. This is file
    transport for one prepared projection, not another dispatch or store.
    """
    from .cicd_source import overlap, write_replace
    from .cicd_v2_runtime import _read
    from .local_intake import strict_json

    if overlap(consuming_workspace, home.target):
        raise CicdError("projection_target_overlap")
    if consuming_workspace == home.workspace:
        write_replace(consuming_workspace / "active-role.json", canonical(pointer))
        return consuming_workspace, pointer
    root = consuming_workspace / "cicd-records" / home.service_id
    queue = [(pointer["input_ref"], True), (pointer["projection_ref"], True)]
    copied, parsed = set(), set()
    opaque = {
        "stdout_ref",
        "stderr_ref",
        "original_ref",
        "log_ref",
        "prompt_ref",
        "skill_ref",
        "result_schema_ref",
        "subject_schema_ref",
        "profile_ref",
        "payload_ref",
        "payload_refs",
        "payloads",
        "owner_ref",
    }
    while queue:
        ref, structured = queue.pop()
        key = (ref["path"], ref["sha256"])
        raw = _read(home.resolve_ref(ref))
        if key not in copied:
            destination = root / ref["path"]
            if any(
                parent.is_symlink() for parent in (destination, *destination.parents)
            ):
                raise CicdError("projection_path_link")
            write_exact(destination, raw, "projection_record_conflict")
            copied.add(key)
        if (
            not structured
            or key in parsed
            or str(ref["path"]).startswith(("originals/", "payloads/"))
        ):
            continue
        parsed.add(key)
        try:
            value = strict_json(raw)
        except IntakeError:
            continue
        values = [(value, True)]
        while values:
            item, follow = values.pop()
            if isinstance(item, dict):
                if set(item) == {"path", "sha256"}:
                    queue.append((item, follow))
                else:
                    values.extend(
                        (
                            child,
                            follow
                            and field not in opaque
                            and not (
                                item.get("format") == "millrace-plus-cicd-input/v2"
                                and field == "evidence_refs"
                            ),
                        )
                        for field, child in item.items()
                    )
            elif isinstance(item, list):
                values.extend((child, follow) for child in item)
    prefix = root.relative_to(consuming_workspace).as_posix()
    projected = pointer | {
        key: dict(pointer[key]) | {"path": prefix + "/" + pointer[key]["path"]}
        for key in ("input_ref", "projection_ref")
    }
    write_replace(consuming_workspace / "active-role.json", canonical(projected))
    return root, projected


def _projection_publication(
    home, projection: Mapping[str, object], kind: str
) -> dict[str, object]:
    from .cicd_v2_runtime import _object, _read

    publication = _object(
        _read(
            home.workspace
            / (str(projection["attempt_id"]) + "." + kind + "-publication.json")
        )
    )
    if _read(home.resolve_ref(publication["projection_ref"])) != canonical(
        dict(projection)
    ):
        raise CicdError("projection_publication_changed")
    suffix = ".facade-role.json" if kind == "facade" else ".candidate-review.json"
    preparation = _object(
        _read(home.workspace / (str(projection["attempt_id"]) + suffix))
    )
    workspace = Path(str(preparation["preparer"]["cwd"]))
    root = (
        workspace
        if workspace == home.workspace
        else workspace / "cicd-records" / home.service_id
    )
    if (
        publication["consuming_workspace"] != str(workspace)
        or publication["artifact_root"] != str(root)
        or publication["projection_ref"] != preparation["projection_ref"]
    ):
        raise CicdError("projection_publication_changed")
    pointer = publication["pointer"]
    prefix = "" if root == workspace else root.relative_to(workspace).as_posix() + "/"
    for key, original in (
        ("input_ref", projection["input_ref"]),
        ("projection_ref", preparation["projection_ref"]),
    ):
        if pointer[key] != dict(original) | {"path": prefix + original["path"]}:
            raise CicdError("projection_publication_changed")
    if _object(_read(workspace / "active-role.json")) != publication["pointer"]:
        raise CicdError("role_pointer_changed")
    for key in ("input_ref", "projection_ref"):
        ref = publication["pointer"][key]
        if hashlib.sha256(_read(workspace / ref["path"])).hexdigest() != ref["sha256"]:
            raise CicdError("projection_record_changed")
    return publication


def accept_candidate_review(
    home: V2Home, projection_ref: object, completion_ref: object
) -> dict[str, object]:
    """Following guard retains one exact review artifact and its public proof."""
    from . import cicd_v2_records as vr
    from .cicd_v2_runtime import _object, _read

    projection = vr.validate_review_projection(home.read_ref(projection_ref))
    preparation = _object(
        _read(
            home.workspace / (str(projection["attempt_id"]) + ".candidate-review.json")
        )
    )
    if preparation["projection_ref"] != projection_ref:
        raise CicdError("review_projection_changed")
    with home.lock(), home.state_lock():
        _active_guard(home, preparation["preparer"], "review")
        saved_path = home.workspace / (
            str(projection["attempt_id"]) + ".candidate-review-accepted.json"
        )
        if saved_path.exists():
            saved = _object(_read(saved_path))
            if saved["completion_ref"] != completion_ref:
                raise CicdError("review_completion_changed")
            home.resolve_ref(saved["review_ref"])
            return saved
        invocation, done = _completed(
            home,
            preparation["preparer"],
            completion_ref,
            expected_attempt=preparation["expected_attempt"],
            expected_node=preparation["expected_node"],
        )
        if (
            done.get("transport") != "completed"
            or done.get("classification") != "semantic"
        ):
            raise CicdError("review_completion_unproven")
        publication = _projection_publication(home, projection, "review")
        document = home.read_ref(projection["input_ref"])
        raw = _read(Path(publication["artifact_root"]) / document["output"])
        review = vr.validate_review(_object(raw))
        candidate = _candidate(home, preparation["candidate_ref"])
        lease = _lease(home, projection["materialization_id"])
        if (
            home.observe()["products"]
            != home.read_ref(preparation["snapshot_ref"])["products"]
        ):
            raise CicdError("noncheck_product_write")
        if (
            lease is None
            or lease["state"] != "sealed"
            or lease["candidate_ref"] != preparation["candidate_ref"]
            or review["candidate_id"] != candidate["candidate_id"]
            or review["materialization_id"] != lease["materialization_id"]
            or review["attempt_id"] != projection["attempt_id"]
            or review["candidate_sha256"] != source_identity(home.observe())
            or review["checks_refs"] != document["checks_refs"]
            or review["patch_ref"] != candidate["delta_ref"]
            or review["verdict"] != done.get("outcome")
            or vr.tree_digest(_tree(home, home.target))
            != candidate["candidate_tree_sha256"]
        ):
            raise CicdError("review_basis_mismatch")
        saved = {
            "attempt_id": projection["attempt_id"],
            "review_ref": home.retain_bytes(raw, ".review.json"),
            "projection_ref": projection_ref,
            "completion_ref": completion_ref,
            "invocation_ref": home.retain(invocation),
            "binding_id": projection["binding_id"],
        }
        write_exact(saved_path, canonical(saved), "review_acceptance_conflict")
        return saved
