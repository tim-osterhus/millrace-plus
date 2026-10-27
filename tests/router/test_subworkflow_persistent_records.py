"""Synthetic codec data only: these fixtures are not runtime or history-fit proof."""

from __future__ import annotations

import copy
import hashlib

import pytest

from millrace_plus import subworkflow_persistent_records as records
from millrace_plus.subworkflow_records import SubworkflowError, canonical, digest

INSTANCE = "1" * 32
RUN = "2" * 32
START = "3" * 32


def control(identifier, *, operation="pause_run", status="applied", run=RUN):
    row = {
        "request_id": identifier,
        "request_digest": "a" * 64,
        "instance_id": INSTANCE,
        "run_id": run,
        "plane_id": None,
        "operation": operation,
        "status": status,
    }
    if status != "queued":
        row.update(state_version=1, retryable=False, code=None)
    return row


@pytest.fixture
def synthetic(tmp_path):
    root = tmp_path.resolve()
    paths = {"workflow": str(root / "workflow"), "project": str(root / "project")}
    settings = {
        "path": ["/usr/bin"],
        "env_allowlist": [],
        "host_paths": {},
        "runtime_defaults": {},
        "operator_policy": {},
    }
    start = control(START, operation="start_run")
    start["instance_state_version"] = 2
    instance = {
        "format": "millrace-router-instance/v2",
        "instance_id": INSTANCE,
        "workflow_file": paths["workflow"] + "/workflow.json",
        "project_root": paths["project"],
        "state_version": 2,
        **settings,
        "runs": [{"id": RUN, "status": "succeeded", "state_version": 8}],
        "controls": [start],
    }
    run = {
        "id": RUN,
        "instance_id": INSTANCE,
        "start_request_id": START,
        "status": "succeeded",
        "state_version": 8,
        "quiescent": True,
        "host_settings": {"state_version": 1, **settings},
        "planes": {},
        "inflight_invocations": [],
        "invocations": [],
        "completions": [],
        "controls": [],
    }
    source = {
        "format": records.LOCAL_SOURCE_FORMAT,
        "service_id": "4" * 32,
        "service_home": str(root / "service"),
        "init_intent_sha256": "b" * 64,
        "core_state_home": str(root / "core"),
        "os_home": "local",
        "os_profile_sha256": "c" * 64,
        "parent_instance_id": "5" * 32,
        "parent_run_id": "6" * 32,
    }
    return {
        "instance": instance,
        "run": run,
        "source": source,
        "paths": paths,
        "settings": settings,
        "root": root,
    }


def reduce(synthetic):
    return records.reduce_full_views(
        synthetic["instance"],
        synthetic["run"],
        instance_id=INSTANCE,
        run_id=RUN,
        start_request_id=START,
        paths=synthetic["paths"],
        settings=synthetic["settings"],
    )


def populate(synthetic, count):
    # UUID-shaped synthetic IDs are intentionally deterministic, not actual runs.
    run = synthetic["run"]
    run["invocations"] = [
        {"id": f"{index + 100:032x}", "status": "completed", "writer_state": "stopped"}
        for index in range(count)
    ]
    run["completions"] = [
        {"invocation_id": row["id"], "effective_writer": "stopped", "stdout": "x" * 256}
        for row in run["invocations"]
    ]


def test_exact_source_binding_and_nonsymlink_locators(synthetic):
    source = synthetic["source"]
    assert (
        records.validate_local_source(
            source,
            expected=copy.deepcopy(source),
        )
        == source
    )
    for changes in (
        {"format": "millrace-plus-subworkflow-call/v2"},
        {"service_id": None},
        {"init_intent_sha256": "B" * 64},
        {"parent_run_id": None},
        {"os_home": "../local"},
        {"extra": True},
    ):
        with pytest.raises(SubworkflowError):
            records.validate_local_source({**source, **changes})
    with pytest.raises(SubworkflowError, match="local_source_mismatch"):
        records.validate_local_source(
            source,
            expected={**source, "service_id": "7" * 32},
        )
    alias = synthetic["root"] / "alias"
    alias.symlink_to(synthetic["root"], target_is_directory=True)
    with pytest.raises(SubworkflowError):
        records.validate_local_source(
            {
                **source,
                "service_home": str(alias / "service"),
            }
        )


def test_exact_bounded_observation_and_no_run_distinction(synthetic):
    observed = reduce(synthetic)
    assert observed["format"] == records.OBSERVATION_FORMAT
    assert observed["settings_sha256"] == digest(synthetic["settings"])
    assert observed["instance_state_version"] == 2
    assert observed["state_version"] == 8
    assert records.closed(observed)
    assert records.closed(observed, attention=True)
    assert records.validate_observation(None, None, None) is None
    assert not records.closed(None)
    for value in (
        {},
        {**observed, "run_id": None},
        {**observed, "format": "unknown"},
        {**observed, "source": "operator_assertion"},
        {**observed, "extra": []},
        {**observed, "state_version": True},
    ):
        with pytest.raises(SubworkflowError):
            records.validate_observation(value, INSTANCE, RUN)
    with pytest.raises(SubworkflowError):
        records.proof_manifest(synthetic["source"], None, {}, {})


def test_expected_paths_start_settings_and_exact_instance_inventory(synthetic):
    observed = reduce(synthetic)
    assert (
        records.validate_observation(
            observed,
            INSTANCE,
            RUN,
            expected_start_request_id=START,
            expected_paths=synthetic["paths"],
            expected_settings=synthetic["settings"],
        )
        == observed
    )
    for kwargs in (
        {"expected_start_request_id": "a" * 32},
        {"expected_paths": {**synthetic["paths"], "project": "/wrong"}},
        {"expected_settings": {**synthetic["settings"], "env_allowlist": ["CHANGED"]}},
    ):
        with pytest.raises(SubworkflowError):
            records.validate_observation(observed, INSTANCE, RUN, **kwargs)
    for mutate in (
        lambda s: s["run"].update(start_request_id="f" * 32),
        lambda s: s["run"]["host_settings"].update(path=["/different"]),
        lambda s: s["instance"]["runs"][0].update(state_version=7),
        lambda s: s["instance"]["runs"].append(copy.deepcopy(s["instance"]["runs"][0])),
        lambda s: s["instance"].update(controls=[]),
        lambda s: s["instance"]["controls"][0].update(instance_state_version=True),
    ):
        changed = copy.deepcopy(synthetic)
        mutate(changed)
        with pytest.raises(SubworkflowError):
            reduce(changed)


def test_large_complete_synthetic_view_reduces_without_old_transport_caps(synthetic):
    populate(synthetic, 10000)
    assert len(canonical(synthetic["run"])) > 2097152
    observed = reduce(synthetic)
    assert len(canonical(observed)) < 2048
    assert observed["counts"]["invocations"]["total"] == 10000
    assert observed["counts"]["completions"]["stopped"] == 10000
    assert "invocations" not in observed
    assert records.closed(observed)
    # This is a codec size assertion, not practical retained-history qualification.


@pytest.mark.parametrize("writer", ["unknown", "running", "may_live"])
def test_last_writer_in_large_view_cannot_disappear(synthetic, writer):
    populate(synthetic, 5000)
    synthetic["run"]["invocations"][-1]["writer_state"] = writer
    synthetic["run"]["completions"][-1]["effective_writer"] = writer
    observed = reduce(synthetic)
    assert observed["counts"]["invocations"][writer] == 1
    assert observed["counts"]["completions"][writer] == 1
    assert not records.closed(observed)


def test_last_row_malformed_and_contradictory_writer_refuses(synthetic):
    populate(synthetic, 3000)
    for mutate in (
        lambda r: r["invocations"][-1].update(writer_state="unrecognized"),
        lambda r: r["invocations"][-1].update(id=r["invocations"][0]["id"]),
        lambda r: r["completions"][-1].update(effective_writer="unknown"),
        lambda r: r["completions"][-1].update(invocation_id="f" * 32),
        lambda r: r["completions"].append(copy.deepcopy(r["completions"][0])),
        lambda r: r.update(inflight_invocations=[r["invocations"][0]["id"]] * 2),
    ):
        changed = copy.deepcopy(synthetic)
        mutate(changed["run"])
        with pytest.raises(SubworkflowError):
            reduce(changed)


def test_all_controls_and_other_runs_are_counted_through_last_row(synthetic):
    run = synthetic["run"]
    run["controls"] = [control(f"{index + 10000:032x}") for index in range(3000)]
    run["controls"].append(control("e" * 32, status="queued"))
    synthetic["instance"]["controls"].append(
        control(
            "f" * 32,
            operation="update_instance_config",
            status="queued",
            run=None,
        )
    )
    synthetic["instance"]["runs"] += [
        {"id": f"{index + 20000:032x}", "status": "succeeded", "state_version": 1}
        for index in range(3000)
    ]
    synthetic["instance"]["runs"].append(
        {
            "id": "d" * 32,
            "status": "ready",
            "state_version": 1,
        }
    )
    observed = reduce(synthetic)
    assert observed["counts"]["run_controls"] == {
        "total": 3001,
        "queued": 1,
        "applied": 3000,
        "refused": 0,
    }
    assert observed["counts"]["instance_controls"]["queued"] == 1
    assert observed["counts"]["other_nonterminal_runs"] == 1
    assert not records.closed(observed)
    for mutate in (
        lambda s: s["run"]["controls"][-1].update(status="unknown"),
        lambda s: s["run"]["controls"][-1].update(request_digest=None),
        lambda s: s["run"]["controls"][-1].update(run_id="b" * 32),
        lambda s: s["run"]["controls"][-1].update(request_id=START),
        lambda s: s["instance"]["runs"][-1].update(status=None),
        lambda s: s["instance"]["controls"][-1].update(run_id=RUN),
    ):
        changed = copy.deepcopy(synthetic)
        mutate(changed)
        with pytest.raises(SubworkflowError):
            reduce(changed)


def test_strict_aggregate_sums_types_and_attention(synthetic):
    observed = reduce(synthetic)
    for mutate in (
        lambda o: o["counts"].update(other_nonterminal_runs=True),
        lambda o: o["counts"]["invocations"].update(total=1),
        lambda o: o["counts"]["run_controls"].update(queued=-1),
        lambda o: o["counts"]["completions"].update(extra=0),
        lambda o: o["counts"].update(inflight_invocations=1),
        lambda o: o.update(quiescent=None),
    ):
        changed = copy.deepcopy(observed)
        mutate(changed)
        with pytest.raises(SubworkflowError):
            records.closed(changed)
    observed["status"] = "paused"
    assert not records.closed(observed)
    assert records.closed(observed, attention=True)
    observed["quiescent"] = False
    assert not records.closed(observed, attention=True)


def proof(synthetic, observed):
    instance_raw = canonical(synthetic["instance"]) + b"\n"
    run_raw = canonical(synthetic["run"]) + b"\n"
    refs = [
        {
            "path": str(synthetic["root"] / name),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        for name, raw in (("instance.json", instance_raw), ("run.json", run_raw))
    ]
    manifest = records.proof_manifest(synthetic["source"], observed, *refs)
    return manifest, instance_raw, run_raw


def test_full_view_proof_exact_bytes_source_identity_and_digest(synthetic):
    observed = reduce(synthetic)
    manifest, instance_raw, run_raw = proof(synthetic, observed)
    kwargs = {
        "local_source": synthetic["source"],
        "observation": observed,
        "instance_view_bytes": instance_raw,
        "run_view_bytes": run_raw,
    }
    assert records.validate_proof(manifest, **kwargs) == manifest
    assert manifest["observation_sha256"] == digest(observed)
    for mutate in (
        lambda p: p.update(run_id=None),
        lambda p: p.update(format="unknown"),
        lambda p: p.update(state_version=True),
        lambda p: p.update(observation_sha256="0" * 64),
        lambda p: p.update(extra=True),
        lambda p: p["local_source"].update(service_id="a" * 32),
        lambda p: p["run_view"].update(sha256="0" * 64),
        lambda p: p["run_view"].update(path=p["instance_view"]["path"]),
        lambda p: p["run_view"].update(extra=True),
    ):
        changed = copy.deepcopy(manifest)
        mutate(changed)
        with pytest.raises(SubworkflowError):
            records.validate_proof(changed, **kwargs)
    with pytest.raises(SubworkflowError):
        records.validate_proof(manifest, **{**kwargs, "run_view_bytes": None})
    no_lf = run_raw.rstrip(b"\n")
    no_lf_manifest = copy.deepcopy(manifest)
    no_lf_manifest["run_view"]["sha256"] = hashlib.sha256(no_lf).hexdigest()
    with pytest.raises(SubworkflowError, match="invalid_view_proof"):
        records.validate_proof(
            no_lf_manifest,
            **{**kwargs, "run_view_bytes": no_lf},
        )


def test_full_bytes_are_reduced_again_instead_of_trusting_summary(synthetic):
    populate(synthetic, 5)
    observed = reduce(synthetic)
    changed = copy.deepcopy(synthetic)
    changed["run"]["invocations"][-1]["writer_state"] = "unknown"
    changed["run"]["completions"][-1]["effective_writer"] = "unknown"
    # The refs really hash these changed bytes; the saved summary is stale.
    manifest, instance_raw, run_raw = proof(changed, observed)
    with pytest.raises(SubworkflowError, match="invalid_view_proof"):
        records.validate_proof(
            manifest,
            local_source=synthetic["source"],
            observation=observed,
            instance_view_bytes=instance_raw,
            run_view_bytes=run_raw,
        )


def test_tagged_call_result_hash_bounds_and_ordinary_mode_rejection(
    synthetic,
    tmp_path,
    monkeypatch,
):
    from persistent_lad_support import FakeClient, advance, journal, setup

    from millrace_plus import subworkflow_v2_records as v2

    fixture = setup(tmp_path, monkeypatch)
    fixture["request"]["parent"]["run_id"] = synthetic["source"]["parent_run_id"]
    fixture["file"].write_bytes(canonical(fixture["request"]) + b"\n")

    class ParentRunClient(FakeClient):
        def read(self, instance, run=None, *, deadline):
            if (
                instance == fixture["request"]["parent"]["instance_id"]
                and run is not None
            ):
                return {"id": run, "instance_id": instance}
            return super().read(instance, run, deadline=deadline)

    advance(fixture, ParentRunClient(fixture))
    storage = journal(fixture)
    retained = storage.load()
    source = {
        **synthetic["source"],
        "parent_instance_id": fixture["request"]["parent"]["instance_id"],
        "parent_run_id": fixture["request"]["parent"]["run_id"],
        "os_profile_sha256": fixture["request"]["os_profile"]["sha256"],
    }
    tagged = {
        **copy.deepcopy(retained),
        "format": records.JOURNAL_FORMAT,
        "local_source": source,
        "observation": None,
    }
    tagged["authority_sha256"] = digest(v2.authority(tagged))
    tagged["record_sha256"] = digest(
        {key: value for key, value in tagged.items() if key != "record_sha256"}
    )
    assert tagged["request_sha256"] == retained["request_sha256"]
    assert (
        v2.validate_journal(
            tagged,
            storage.home,
            storage.key,
            _local_source=source,
        )
        == tagged
    )
    assert storage.path.read_bytes() == canonical(retained) + b"\n"
    with pytest.raises(SubworkflowError):
        v2.validate_journal(tagged, storage.home, storage.key)
    with pytest.raises(SubworkflowError):
        v2.validate_journal(
            {**tagged, "claim_sha256": None},
            storage.home,
            storage.key,
            _local_source=source,
        )
    with pytest.raises(SubworkflowError):
        v2.validate_journal(
            {**tagged, "record_sha256": "0" * 64},
            storage.home,
            storage.key,
            _local_source=source,
        )
    changed_source = {**source, "init_intent_sha256": "f" * 64}
    assert (
        digest(v2.authority({**tagged, "local_source": changed_source}))
        != tagged["authority_sha256"]
    )
    result = v2.result_manifest(tagged, "CHILD_UNCERTAIN", "result_limit")
    assert result["format"] == records.RESULT_FORMAT
    assert result["status"] is None and result["observation"] is None
    assert v2.validate_result(result, _local_source=source) == result
    with pytest.raises(SubworkflowError):
        v2.validate_result(result)
    with pytest.raises(SubworkflowError):
        v2.validate_result({**result, "status": "running"}, _local_source=source)
    with pytest.raises(SubworkflowError):
        v2.validate_result({**result, "observation": {}}, _local_source=source)
    with pytest.raises(SubworkflowError):
        v2.validate_result(result, _local_source=changed_source)
    for limit in (v2.JOURNAL_LIMIT, v2.RESULT_LIMIT):
        with pytest.raises(SubworkflowError, match="record_too_large"):
            v2.bounded({"padding": "x" * limit}, limit)
    # Ordinary retained bytes and ordinary marker remain accepted unchanged.
    assert v2.validate_journal(retained, storage.home, storage.key) == retained
