from __future__ import annotations

import sys

import pytest

from millrace_plus.demo import hardware


@pytest.mark.parametrize("arm64,expected", [("1", True), ("0", False)])
def test_mac_collection_fixed_probes_and_no_identifiers(monkeypatch, arm64, expected):
    calls = []
    values = {
        "hw.memsize": str(16 * 1024**3),
        "hw.logicalcpu": "8",
        "hw.optional.arm64": arm64,
    }

    def probe(args):
        calls.append(args)
        return (
            (values[args[-1]], None)
            if args[0] == "sysctl"
            else (
                '{"SPDisplaysDataType":[{"sppci_model":"GPU","serial":"SECRET"}]}',
                None,
            )
        )

    monkeypatch.setattr(hardware.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        hardware.platform, "machine", lambda: "arm64" if expected else "x86_64"
    )
    monkeypatch.setattr(hardware, "_probe", probe)
    result = hardware.collect_hardware()
    assert result["memory"]["unified"] is expected
    assert result["logical_cpus"] == 8
    assert len(calls) == 4
    assert "SECRET" not in str(result)


def test_linux_sysfs_numeric_identity_and_nvidia_dedup(tmp_path, monkeypatch):
    sysfs = tmp_path / "drm"
    for card, vendor in ((0, "0x1002"), (1, "0x10de")):
        path = sysfs / f"card{card}" / "device"
        path.mkdir(parents=True)
        for name, value in {
            "vendor": vendor,
            "device": "0x1234",
            "mem_info_vram_total": "8192",
            "mem_info_vram_used": "2048",
        }.items():
            (path / name).write_text(value)
    result = hardware._linux_gpus(sysfs)
    assert [g["device_id"] for g in result] == ["0x1002:0x1234", "0x10de:0x1234"]
    assert all(g["model"] is None and g["free_bytes"] == 6144 for g in result)


@pytest.mark.parametrize(
    "failure", ["missing", "permission_denied", "timeout", "invalid_output"]
)
def test_missing_mac_facts_have_reasons(monkeypatch, failure):
    monkeypatch.setattr(hardware.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(hardware, "_probe", lambda args: (None, failure))
    result = hardware.collect_hardware()
    assert result["memory"]["physical_bytes"] is None
    assert result["memory"]["unknown_reasons"]["physical_bytes"] == failure
    assert result["logical_cpus"] is None
    assert result["probe_status"] == "partial"


def test_probe_size_and_timeout_are_fixed(monkeypatch, tmp_path):
    # Only a test-owned Python child runs, never any native adapter from PATH.
    monkeypatch.setattr(hardware.shutil, "which", lambda name: sys.executable)
    text, reason = hardware._probe(["test-python", "-c", "print('x' * 70000)"])
    assert text is None and reason == "invalid_output"
    monkeypatch.setattr(hardware, "PROBE_TIMEOUT", 0.05)
    text, reason = hardware._probe(["test-python", "-c", "import time; time.sleep(1)"])
    assert text is None and reason == "timeout"
