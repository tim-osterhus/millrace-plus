"""Integer-only estimates for the three frozen full-shard GGUF references."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

from .contracts import DemoError, validate_hardware

__all__ = ["build_baseline", "load_reference", "estimate_scenario"]

REFERENCE_VERSION = "hardware-demo-shortlist/v1"
ESTIMATOR_VERSION = "hardware-demo-estimator/v1"
TIERS = (2048, 4096, 8192, 16384, 32768)
EXPLANATION = (
    "These are memory-budget estimates for three saved reference models, using one "
    "sequence and FP16 KV cache. Actual fit depends on backend allocations and other "
    "applications, and context includes both your prompt and generated output. Speed "
    "and prefill are unknown because this demo does not run a model."
)
LIMITATIONS = [
    "Estimated context; loading and fit are not guaranteed.",
    "Memory budgets and runtime overhead are provisional planning assumptions, "
    "not measurements.",
    "Throughput and prefill are unknown; no model was run.",
    "Backend compatibility is unverified; multi-device placement is unsupported.",
    "Reference metadata is frozen at 2026-10-24, not a newest-model ranking.",
]


def load_reference() -> dict[str, Any]:
    reference = files("millrace_plus").joinpath(
        "workflows/hardware-demo/references/shortlist.json"
    )
    value = json.loads(reference.read_text(encoding="utf-8"))
    if type(value) is not dict:
        raise DemoError("invalid_reference_document")
    return value


def estimate_scenario(
    model: dict[str, Any],
    *,
    scenario_id: str,
    total: int | None,
    available: int | None,
    discrete: bool = False,
) -> dict[str, Any]:
    """A single pool, never RAM+VRAM or a sum of separate GPUs."""
    weight = model["stored_weight_bytes"]
    overhead = max(512 * 1024**2, (weight + 9) // 10)
    kv = model["layers"] * model["kv_heads"] * model["head_dim"] * 4
    percentage = 80 if discrete else 60
    pool = total * percentage // 100 if total is not None else None
    if pool is not None and available is not None:
        pool = min(pool, available)
    supported = (
        model["architecture"] == "qwen2" and model["use_sliding_window"] is False
    )
    capacity = (
        (pool - weight - overhead) // kv if pool is not None and supported else None
    )
    context = (
        max(
            (tier for tier in TIERS if tier <= min(capacity, model["max_context"])),
            default=None,
        )
        if capacity is not None
        else None
    )
    limitations = ["Estimated context; loading and fit are not guaranteed."]
    if available is None:
        limitations.append("Current free memory is unobserved.")
    if not supported:
        limitations.append("Cache architecture is outside the reviewed estimator.")
    return {
        "scenario_id": scenario_id,
        "pool_bytes": pool,
        "budget_policy": f"{percentage}% physical pool, clipped by known free memory",
        "weight_bytes": weight,
        "overhead_bytes": overhead,
        "kv_bytes_per_token": kv,
        "cache_type": "fp16",
        "context_cap": model["max_context"],
        "context_tier": context,
        "fit_status": (
            "unknown"
            if capacity is None
            else "estimated"
            if context is not None
            else "insufficient_budget"
        ),
        "backend_compatibility": "unverified",
        "limitations": limitations,
    }


def build_baseline(hardware: dict[str, Any]) -> dict[str, Any]:
    validate_hardware(hardware)
    memory = hardware["memory"]
    entries = []
    for model in load_reference()["models"]:
        scenarios = [
            estimate_scenario(
                model,
                scenario_id="cpu",
                total=memory["physical_bytes"],
                available=memory["available_bytes"],
            )
        ]
        if hardware["platform"] == "Darwin" and memory["unified"] is True:
            scenarios.append(
                estimate_scenario(
                    model,
                    scenario_id="unified_metal_possible",
                    total=memory["physical_bytes"],
                    available=memory["available_bytes"],
                )
            )
        else:
            for index, gpu in enumerate(hardware["gpus"]):
                scenarios.append(
                    estimate_scenario(
                        model,
                        scenario_id=f"discrete_gpu_{index + 1}",
                        total=gpu["total_bytes"],
                        available=gpu["free_bytes"],
                        discrete=True,
                    )
                )
        entries.append(
            {
                "reference_id": model["reference_id"],
                "reference": model,
                "scenarios": scenarios,
            }
        )
    result = {
        "format": "millrace-demo-baseline/v1",
        "reference_version": REFERENCE_VERSION,
        "estimator_version": ESTIMATOR_VERSION,
        "hardware": hardware,
        "entries": entries,
        "explanation": EXPLANATION,
        "limitations": list(LIMITATIONS),
        "throughput_status": "unknown",
    }
    from .render import baseline_display

    result["display"] = baseline_display(result)
    return result
