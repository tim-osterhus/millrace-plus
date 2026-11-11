from __future__ import annotations

import copy
import hashlib

import pytest

from millrace_plus.demo.contracts import (
    DemoError,
    canonical_bytes,
    load_document,
    safe_url,
    validate_baseline,
    validate_candidate,
    validate_hardware,
    validate_report,
)
from millrace_plus.demo.estimate import (
    build_baseline,
    estimate_scenario,
    load_reference,
)
from millrace_plus.demo.hardware import (
    parse_displays,
    parse_meminfo,
    parse_nvidia,
    unknown_hardware,
)
from millrace_plus.demo.render import build_report, standalone_html


@pytest.mark.parametrize(
    "gib,expected",
    [
        (8, [32768, None, None]),
        (16, [32768, 32768, None]),
        (40, [32768, 32768, 8192]),
        (64, [32768, 32768, 32768]),
    ],
)
def test_reviewed_size_span(hardware, gib, expected):
    hardware["memory"]["physical_bytes"] = gib * 1024**3
    baseline = validate_baseline(build_baseline(hardware))
    assert [
        entry["scenarios"][0]["context_tier"] for entry in baseline["entries"]
    ] == expected
    assert baseline["throughput_status"] == "unknown"
    assert "not guaranteed" in baseline["limitations"][0]
    assert canonical_bytes(baseline) == canonical_bytes(build_baseline(hardware))


def test_full_shards_and_fp16_kv():
    models = load_reference()["models"]
    assert [len(m["artifacts"]) for m in models] == [1, 2, 5]
    assert [m["stored_weight_bytes"] for m in models] == [
        2104932768,
        4683073632,
        19851336384,
    ]
    assert [sum(a["stored_bytes"] for a in m["artifacts"]) for m in models] == [
        m["stored_weight_bytes"] for m in models
    ]
    assert [m["layers"] * m["kv_heads"] * m["head_dim"] * 4 for m in models] == [
        36864,
        57344,
        262144,
    ]
    assert all(len(a["sha256"]) == 64 for m in models for a in m["artifacts"])


@pytest.mark.parametrize("tier", [2048, 4096, 8192, 16384, 32768])
@pytest.mark.parametrize("delta", [-1, 0])
def test_exact_tier_thresholds(tier, delta):
    model = load_reference()["models"][0]
    base = estimate_scenario(model, scenario_id="cpu", total=1024**4, available=0)
    required = (
        base["weight_bytes"]
        + base["overhead_bytes"]
        + tier * base["kv_bytes_per_token"]
    )
    result = estimate_scenario(
        model, scenario_id="cpu", total=1024**4, available=required + delta
    )
    previous = {2048: None, 4096: 2048, 8192: 4096, 16384: 8192, 32768: 16384}
    assert result["context_tier"] == (previous[tier] if delta else tier)


def test_negative_unknown_model_cap_and_other_cache():
    model = load_reference()["models"][0]
    assert (
        estimate_scenario(model, scenario_id="cpu", total=1, available=None)[
            "fit_status"
        ]
        == "insufficient_budget"
    )
    assert (
        estimate_scenario(model, scenario_id="cpu", total=None, available=None)[
            "fit_status"
        ]
        == "unknown"
    )
    assert (
        estimate_scenario(model, scenario_id="cpu", total=1024**4, available=None)[
            "context_tier"
        ]
        == 32768
    )
    model["use_sliding_window"] = True
    assert (
        estimate_scenario(model, scenario_id="cpu", total=1024**4, available=None)[
            "fit_status"
        ]
        == "unknown"
    )


def test_unified_and_discrete_never_sum_pools(hardware):
    hardware.update(platform="Darwin", architecture="arm64")
    hardware["memory"]["unified"] = True
    hardware["gpus"] = parse_displays(
        '{"SPDisplaysDataType":[{"sppci_model":"Apple M4",'
        '"spdisplays_vram_shared":"16 GB"}]}',
        unified=True,
    )
    result = build_baseline(hardware)
    scenarios = result["entries"][0]["scenarios"]
    assert [s["scenario_id"] for s in scenarios] == ["cpu", "unified_metal_possible"]
    assert scenarios[0]["pool_bytes"] == scenarios[1]["pool_bytes"]
    hardware.update(platform="Linux", architecture="x86_64")
    hardware["memory"]["unified"] = False
    hardware["gpus"] = parse_nvidia("GPU A, 8192, 4096\nGPU B, 8192, 8192\n")
    scenarios = build_baseline(hardware)["entries"][0]["scenarios"]
    assert len(scenarios) == 3
    assert scenarios[1]["pool_bytes"] == 4096 * 1024**2
    assert scenarios[2]["pool_bytes"] == 8192 * 1024**2 * 80 // 100


@pytest.mark.parametrize(
    "text,expected",
    [
        ("MemTotal: 1024 kB\nMemAvailable: 512 kB", (1048576, 524288)),
        ("MemTotal: 1024 kB", (1048576, None)),
        ("MemTotal: bad kB", (None, None)),
        ("MemTotal: 1 kB\nMemTotal: 2 kB", (None, None)),
        ("MemTotal: 1 kB\nMemAvailable: 2 kB", (None, None)),
    ],
)
def test_meminfo(text, expected):
    assert parse_meminfo(text) == expected


@pytest.mark.parametrize(
    "text", ["GPU, N/A, 12", "GPU, 10, 12", "onefield", "GPU, 10, -1"]
)
def test_bad_nvidia(text):
    with pytest.raises(ValueError):
        parse_nvidia(text)


def test_mac_discrete_and_sanitized_display():
    raw = (
        '{"SPDisplaysDataType":[{"sppci_model":"Radeon",'
        '"spdisplays_vram":"8 GB","serial":"secret"}]}'
    )
    gpu = parse_displays(raw, unified=False)[0]
    assert gpu["total_bytes"] == 8 * 1024**3
    assert "secret" not in str(gpu) and "serial" not in gpu


@pytest.mark.parametrize(
    "mutation",
    [
        lambda h: h.update(logical_cpus=True),
        lambda h: h["memory"].update(physical_bytes=-1),
        lambda h: h["memory"].update(extra=1),
        lambda h: h["memory"].update(available_bytes=0),
        lambda h: h.update(environment={}),
    ],
)
def test_hardware_refusals(hardware, mutation):
    mutation(hardware)
    with pytest.raises(DemoError):
        validate_hardware(hardware)


def test_unknown_document_and_display_consistency(hardware):
    unknown = validate_baseline(build_baseline(unknown_hardware()))
    assert all(e["scenarios"][0]["fit_status"] == "unknown" for e in unknown["entries"])
    baseline = build_baseline(hardware)
    changed = copy.deepcopy(baseline)
    changed["display"]["facts"][0]["value"] = "invented"
    with pytest.raises(DemoError, match="baseline_mismatch"):
        validate_baseline(changed)
    changed = copy.deepcopy(baseline)
    changed["entries"][0]["scenarios"][0]["context_tier"] = 999
    with pytest.raises(DemoError):
        validate_baseline(changed)
    assert (
        hashlib.sha256(canonical_bytes(baseline)).digest()
        == hashlib.sha256(canonical_bytes(build_baseline(hardware))).digest()
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/a",
        "https://localhost/a",
        "https://127.0.0.1/a",
        "https://[::1]/a",
        "https://10.0.0.1/a",
        "https://user:pass@example.com/a",
        "file:///tmp/a",
        "javascript:alert(1)",
        "https://example.com:9000/a",
        "https://example.com/ x",
    ],
)
def test_unsafe_sources(url):
    with pytest.raises(DemoError):
        safe_url(url)


def test_safe_source_and_report(hardware, candidate):
    assert safe_url("https://huggingface.co/Qwen/model")
    validate_candidate(candidate, no_web=True)
    baseline = build_baseline(hardware)
    report = build_report(
        "demo",
        baseline,
        native={
            "status": "completed",
            "phase": "main",
            "reason": None,
            "candidate": candidate,
        },
    )
    validate_report(report)
    html = standalone_html(report)
    assert "Qwen2.5 3B" in html and "<script" not in html
    assert report["baseline"] == baseline
    report["display"]["tables"][0]["rows"][0][2] = "guaranteed"
    with pytest.raises(DemoError):
        validate_report(report)


@pytest.mark.parametrize(
    "change",
    [
        {"hardware": {}},
        {"summary": "One sentence."},
        {"summary": "Guaranteed fit. Very fast."},
        {"summary": "Speed is 99 tokens/sec. It works."},
        {"summary": "<script>alert(1)</script>. Bad."},
        {"summary": "a" * 1201},
        {"research_status": "secret"},
    ],
)
def test_candidate_refusals(candidate, change):
    candidate.update(change)
    with pytest.raises(DemoError):
        validate_candidate(candidate)


def test_saved_no_web_candidate_accepts_its_negated_guarantee():
    # Exact candidate content retained from the first D2 no-web journey. The
    # native main completed; the old guarantee-token guard rejected its caveat.
    candidate = {
        "format": "millrace-demo-candidate/v1",
        "research_status": "skipped",
        "summary": "The baseline reports a Linux x86_64 host with 8 logical CPUs, "
        "about 12.3 GB physical RAM and about 6.1 GB available, plus an Intel GPU "
        "(0x8086:0x8a56) with unknown memory. On the CPU pool, the provisional "
        "estimated context is 32768 tokens for Qwen2.5 3B Instruct Q4_K_M and "
        "8192 for the 7B, while the 32B is marked insufficient_budget; loading "
        "and fit are not guaranteed, and speed and prefill are unknown. Web "
        "research was disabled, so no alternatives or published measurements "
        "are proposed.",
        "suggestions": [],
        "published_measurements": [],
    }
    assert validate_candidate(candidate, no_web=True) == candidate


def test_saved_no_web_candidate_accepts_provisional_baseline_context():
    # Exact candidate content from the second D2 no-web journey. Its context
    # numbers restate the baseline; they introduce no suggestion or measurement.
    candidate = {
        "format": "millrace-demo-candidate/v1",
        "research_status": "skipped",
        "summary": "The baseline reports a Linux x86_64 host with 8 logical CPUs, "
        "12300804096 bytes of physical RAM and 6121086976 bytes available, with "
        "GPU memory unknown. Its provisional CPU estimates are 32768 context "
        "tokens for Qwen2.5 3B Instruct Q4_K_M, 8192 for Qwen2.5 7B Instruct "
        "Q4_K_M, and insufficient budget for Qwen2.5 32B Instruct Q4_K_M; "
        "loading and fit are not guaranteed, and speed and prefill are unknown "
        "because no model was run. Web research was disabled, so no alternatives "
        "or published measurements are proposed.",
        "suggestions": [],
        "published_measurements": [],
    }
    assert validate_candidate(candidate, no_web=True) == candidate


@pytest.mark.parametrize("negation", ["not guaranteed", "NOT GUARANTEED"])
def test_candidate_accepts_negated_guarantee(candidate, negation):
    candidate["summary"] = f"Loading and fit are {negation}. Host speed is unknown."
    assert validate_candidate(candidate, no_web=True) == candidate


@pytest.mark.parametrize(
    "claim",
    [
        "Fit is guaranteed",
        "It will definitely fit",
        "It will load",
        "It will run",
        "Speed is 99 tokens/sec",
        "Speed is 99 tps",
        "Loading is not guaranteed, but will fit",
        "Loading is not guaranteed, but fit is guaranteed",
    ],
)
@pytest.mark.parametrize("field", ["summary", "rationale"])
def test_negated_guarantee_does_not_hide_unsupported_claims(candidate, claim, field):
    if field == "summary":
        candidate["summary"] = f"{claim}. Host speed is unknown."
    else:
        candidate["research_status"] = "completed"
        candidate["suggestions"] = [
            {
                "model_id": "model",
                "artifact_url": "https://example.com/model",
                "source_urls": ["https://example.com/source"],
                "rationale": claim,
            }
        ]
    with pytest.raises(DemoError, match="unsupported_candidate_claim"):
        validate_candidate(candidate)


@pytest.mark.parametrize(
    "rationale",
    ["It has 32k context", "Its provisional estimate is 32768 context tokens"],
)
def test_suggestion_cannot_introduce_numerical_context(candidate, rationale):
    candidate["research_status"] = "completed"
    candidate["suggestions"] = [
        {
            "model_id": "model",
            "artifact_url": "https://example.com/model",
            "source_urls": ["https://example.com/source"],
            "rationale": rationale,
        }
    ]
    with pytest.raises(DemoError, match="unsupported_candidate_claim"):
        validate_candidate(candidate)


def test_candidate_sources_no_web_and_oversize(candidate):
    candidate["research_status"] = "completed"
    candidate["suggestions"] = [
        {
            "model_id": "model",
            "artifact_url": "https://example.com/model",
            "source_urls": [],
            "rationale": "An alternative.",
        }
    ]
    with pytest.raises(DemoError, match="missing_candidate_source"):
        validate_candidate(candidate)
    candidate["suggestions"][0]["source_urls"] = ["https://example.com/source"]
    validate_candidate(candidate)
    with pytest.raises(DemoError, match="unexpected_research"):
        validate_candidate(candidate, no_web=True)
    candidate["suggestions"][0]["rationale"] = "x" * 40000
    with pytest.raises(DemoError):
        validate_candidate(candidate)


@pytest.mark.parametrize(
    "raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b"[]", b"\xff"]
)
def test_strict_file_parser(tmp_path, raw):
    path = tmp_path / "input.json"
    path.write_bytes(raw)
    with pytest.raises(DemoError):
        load_document(path)


def test_read_bounds_and_symlink(tmp_path):
    path = tmp_path / "input.json"
    path.write_bytes(b"{}" * 20)
    with pytest.raises(DemoError, match="document_too_large"):
        load_document(path, limit=10)
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(OSError):
        load_document(link)
