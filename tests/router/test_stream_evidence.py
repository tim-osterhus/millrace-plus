"""Native diagnostics stay local while byte ownership supplies bounded facts."""

from millrace_plus.router import codex
from millrace_plus.router.setting_layers import merge_setting_layers


def test_redaction_caps_and_accepted_classifications() -> None:
    secret = "inert-secret-value"
    native = ("Bearer " + secret + " " + secret + "\n").encode() * 10000
    diagnostic, truncated = codex._stderr(
        native[:65536], True, {"TOKEN": secret}, "execution_failure"
    )
    assert len(diagnostic) <= 65536 and truncated
    assert secret.encode() not in diagnostic
    record, cut = codex._record(
        b"[allowed] ", secret + "\x1b" * 3000, {"TOKEN": secret}
    )
    assert len(record) <= 1024 and cut
    assert secret.encode() not in record and b"\x1b" not in record


def test_merge_does_not_choose_layer_order_or_fill_omissions() -> None:
    layers = (("runtime", {"model": "runtime"}), ("profile", {"model": "profile"}))
    assert merge_setting_layers({"model"}, *layers)["effective"] == {"model": "profile"}
    assert merge_setting_layers({"model"}, *reversed(layers))["effective"] == {
        "model": "runtime"
    }
    assert merge_setting_layers({"model"}) == {
        "requested": {}, "effective": {}, "provenance": {}, "warnings": [],
    }
