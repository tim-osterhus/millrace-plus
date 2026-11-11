"""Passive bounded hardware observations, with fixture-friendly parsers."""

from __future__ import annotations

import csv
import io
import json
import os
import platform
import re
import selectors
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .contracts import validate_hardware

__all__ = [
    "collect_hardware",
    "unknown_hardware",
    "parse_meminfo",
    "parse_nvidia",
    "parse_displays",
]

PROBE_TIMEOUT = 3.0
PROBE_LIMIT = 64 * 1024
_GIB = 1024**3
_MIB = 1024**2


def _nulls(values: dict[str, Any], reason: str) -> dict[str, str]:
    return {key: reason for key, value in values.items() if value is None}


def unknown_hardware(reason: str = "probe_failed") -> dict[str, Any]:
    return {
        "format": "millrace-demo-hardware/v1",
        "platform": None,
        "architecture": None,
        "logical_cpus": None,
        "unknown_reasons": {
            "platform": reason,
            "architecture": reason,
            "logical_cpus": reason,
        },
        "memory": {
            "physical_bytes": None,
            "available_bytes": None,
            "unified": None,
            "unknown_reasons": {
                "physical_bytes": reason,
                "available_bytes": reason,
                "unified": reason,
            },
        },
        "gpus": [],
        "probe_status": "unavailable",
    }


def _probe(args: list[str]) -> tuple[str | None, str | None]:
    executable = shutil.which(args[0])
    if executable is None:
        return None, "missing"
    try:
        process = subprocess.Popen(
            [executable, *args[1:]],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
    except PermissionError:
        return None, "permission_denied"
    except OSError:
        return None, "probe_failed"
    assert process.stdout is not None  # stdout=PIPE always provides this owned stream.
    output = bytearray()
    failure = None
    deadline = time.monotonic() + PROBE_TIMEOUT
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    failure = "timeout"
                    break
                if not selector.select(remaining):
                    failure = "timeout"
                    break
                chunk = os.read(
                    process.stdout.fileno(), min(4096, PROBE_LIMIT + 1 - len(output))
                )
                if not chunk:
                    selector.unregister(process.stdout)
                    break
                output.extend(chunk)
                if len(output) > PROBE_LIMIT:
                    failure = "invalid_output"
                    break
        if failure is None:
            try:
                process.wait(timeout=max(0.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                failure = "timeout"
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()
    if failure:
        return None, failure
    if process.returncode != 0:
        return None, "probe_failed"
    try:
        return output.decode("utf-8"), None
    except UnicodeError:
        return None, "invalid_output"


def _read(path: Path) -> tuple[str | None, str | None]:
    try:
        with path.open("rb") as stream:
            raw = stream.read(PROBE_LIMIT + 1)
        if len(raw) > PROBE_LIMIT:
            return None, "invalid_output"
        return raw.decode("utf-8"), None
    except FileNotFoundError:
        return None, "missing"
    except PermissionError:
        return None, "permission_denied"
    except (OSError, UnicodeError):
        return None, "invalid_output"


def parse_meminfo(text: str) -> tuple[int | None, int | None]:
    """Extract only MemTotal/MemAvailable, refusing duplicate or malformed facts."""
    fields: dict[str, int] = {}
    for line in text.splitlines():
        key = line.partition(":")[0]
        if key not in {"MemTotal", "MemAvailable"}:
            continue
        match = re.fullmatch(r"(MemTotal|MemAvailable):\s+(\d+)\s+kB\s*", line)
        if match is None or key in fields:
            return None, None
        fields[key] = int(match[2]) * 1024
    total, available = fields.get("MemTotal"), fields.get("MemAvailable")
    if total is not None and available is not None and available > total:
        return None, None
    return total, available


def _gpu(
    source: str,
    model: str | None = None,
    device_id: str | None = None,
    total: int | None = None,
    free: int | None = None,
    reason: str = "missing",
) -> dict[str, Any]:
    values = {
        "model": model,
        "device_id": device_id,
        "total_bytes": total,
        "free_bytes": free,
    }
    return {**values, "source": source, "unknown_reasons": _nulls(values, reason)}


def parse_nvidia(text: str) -> list[dict[str, Any]]:
    """Parse NVIDIA's requested MiB CSV; no version or driver readiness inference."""
    result: list[dict[str, Any]] = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) != 3 or len(result) == 8:
            raise ValueError("invalid NVIDIA CSV")
        name, total, free = (part.strip() for part in row)
        if not name or not total.isdecimal() or not free.isdecimal():
            raise ValueError("invalid NVIDIA CSV")
        if int(free) > int(total):
            raise ValueError("invalid NVIDIA memory")
        result.append(
            _gpu("nvidia-smi", name, total=int(total) * _MIB, free=int(free) * _MIB)
        )
    return result


def parse_displays(text: str, *, unified: bool) -> list[dict[str, Any]]:
    """Read display model/VRAM fields only; never retain serial/display identifiers."""
    data = json.loads(text)
    if type(data) is not dict or type(data.get("SPDisplaysDataType")) is not list:
        raise ValueError("invalid displays document")
    result: list[dict[str, Any]] = []
    for item in data["SPDisplaysDataType"][:8]:
        if type(item) is not dict:
            raise ValueError("invalid display")
        model = item.get("sppci_model") or item.get("_name")
        if type(model) is not str or not model or len(model) > 160:
            model = None
        total = None
        if not unified:
            vram = item.get("spdisplays_vram") or item.get("spdisplays_vram_shared")
            match = re.fullmatch(r"(\d+)\s*(MB|GB)", vram or "")
            # Shared GPU memory belongs to system RAM, never a discrete pool.
            if match and "spdisplays_vram" in item:
                total = int(match[1]) * (_GIB if match[2] == "GB" else _MIB)
        result.append(
            _gpu(
                "system_profiler",
                model,
                total=total,
                reason="unsupported" if unified else "missing",
            )
        )
    return result


def _linux_gpus(sysfs: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for device in sorted(sysfs.glob("card[0-9]*/device"))[:8]:
        vendor, _ = _read(device / "vendor")
        identity, _ = _read(device / "device")
        identifier = None
        if (
            vendor
            and identity
            and re.fullmatch(r"0x[0-9a-fA-F]{4}\s*", vendor)
            and re.fullmatch(r"0x[0-9a-fA-F]{4}\s*", identity)
        ):
            identifier = f"{vendor.strip()}:{identity.strip()}"
        total, _ = _read(device / "mem_info_vram_total")
        used, _ = _read(device / "mem_info_vram_used")
        total_bytes = (
            int(total.strip()) if total and total.strip().isdecimal() else None
        )
        used_bytes = int(used.strip()) if used and used.strip().isdecimal() else None
        free = (
            total_bytes - used_bytes
            if total_bytes is not None
            and used_bytes is not None
            and used_bytes <= total_bytes
            else None
        )
        result.append(_gpu("sysfs", device_id=identifier, total=total_bytes, free=free))
    return result


def collect_hardware() -> dict[str, Any]:
    """Run only the fixed passive probes for this platform, with 3s/64KiB bounds."""
    name = platform.system()
    result = unknown_hardware("unsupported")
    result.update(
        platform=name or None,
        architecture=platform.machine() or None,
        logical_cpus=os.cpu_count(),
    )
    result["unknown_reasons"] = _nulls(
        {key: result[key] for key in ("platform", "architecture", "logical_cpus")},
        "missing",
    )
    memory = result["memory"]
    if name == "Linux":
        text, reason = _read(Path("/proc/meminfo"))
        total, available = parse_meminfo(text) if text is not None else (None, None)
        memory.update(physical_bytes=total, available_bytes=available, unified=False)
        memory["unknown_reasons"] = _nulls(
            {
                key: memory[key]
                for key in ("physical_bytes", "available_bytes", "unified")
            },
            reason or "invalid_output",
        )
        nvidia, _ = _probe(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.free",
                "--format=csv,noheader,nounits",
            ]
        )
        gpus = []
        if nvidia is not None:
            try:
                gpus = parse_nvidia(nvidia)
            except ValueError:
                pass
        # Use one source per device; NVIDIA CSV and sysfs are not additive.
        sysfs = _linux_gpus(Path("/sys/class/drm"))
        result["gpus"] = (
            (
                gpus
                + [
                    g
                    for g in sysfs
                    if g["device_id"] and not g["device_id"].startswith("0x10de:")
                ]
            )[:8]
            if gpus
            else sysfs
        )
    elif name == "Darwin":
        values = {}
        errors = {}
        for key in ("hw.memsize", "hw.logicalcpu", "hw.optional.arm64"):
            raw, error = _probe(["sysctl", "-n", key])
            values[key] = int(raw.strip()) if raw and raw.strip().isdecimal() else None
            errors[key] = error or "invalid_output"
        unified = (
            values["hw.optional.arm64"] == 1
            if values["hw.optional.arm64"] is not None
            else None
        )
        result["logical_cpus"] = values["hw.logicalcpu"] or None
        result["unknown_reasons"] = _nulls(
            {key: result[key] for key in ("platform", "architecture", "logical_cpus")},
            errors["hw.logicalcpu"],
        )
        memory.update(
            physical_bytes=values["hw.memsize"], available_bytes=None, unified=unified
        )
        memory["unknown_reasons"] = {"available_bytes": "unsupported"}
        if memory["physical_bytes"] is None:
            memory["unknown_reasons"]["physical_bytes"] = errors["hw.memsize"]
        if unified is None:
            memory["unknown_reasons"]["unified"] = errors["hw.optional.arm64"]
        displays, _ = _probe(["system_profiler", "SPDisplaysDataType", "-json"])
        if displays is not None:
            try:
                result["gpus"] = parse_displays(displays, unified=unified is not False)
            except (ValueError, TypeError):
                pass
    result["probe_status"] = (
        "partial"
        if (
            result["unknown_reasons"]
            or memory["unknown_reasons"]
            or any(g["unknown_reasons"] for g in result["gpus"])
        )
        else "complete"
    )
    return validate_hardware(result)
