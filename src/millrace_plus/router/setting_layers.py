"""Deterministic merge mechanics for caller-ordered, already validated settings."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

__all__ = ("merge_setting_layers",)


def merge_setting_layers(
    keys: Iterable[str], *layers: tuple[str, Mapping[str, object]]
) -> dict[str, object]:
    """Merge low-to-high layers; callers own order, conflicts and requiredness."""
    selected = frozenset(keys)
    values: dict[str, object] = {}
    provenance: dict[str, object] = {}
    for source, layer in layers:
        for key in sorted(selected & layer.keys()):
            values[key] = layer[key]
            provenance[key] = {"source": source, "policy": None}
    return {
        "requested": values.copy(),
        "effective": values,
        "provenance": provenance,
        "warnings": [],
    }
