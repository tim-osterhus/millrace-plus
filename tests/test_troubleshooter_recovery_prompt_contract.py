from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = PROJECT_ROOT / "millrace_workflow_package" / "assets" / "workflows"


def test_formal_recovery_markers_are_scoped_to_rendered_terminal_options() -> None:
    entrypoints = (
        ASSET_ROOT / "execution.lad/entrypoints/lad_troubleshooter.md",
        ASSET_ROOT / "lad.full.pi/entrypoints/lad_troubleshooter.md",
    )
    skill = (ASSET_ROOT / "execution.lad/skills/troubleshooter-core.md").read_text()

    for path in entrypoints:
        text = path.read_text()
        assert "TROUBLESHOOT_RECOVERED" in text
        assert "TROUBLESHOOT_QUARANTINE" in text
        assert "exact marker/artifact pair" in text
        assert "terminal_options` renders its exact marker/artifact pair" in text
        assert "rendered in `terminal_options` for the current recovery attempt" in text
        assert (
            "when selected dispatch context asks for recovered-source evidence"
            not in text
        )
        assert (
            "when selected dispatch context asks for unresolved recovery evidence"
            not in text
        )
        assert (
            "use a legal blocked or quarantine path rendered by dispatch" not in text
        )
        assert "TROUBLESHOOT_COMPLETE" in text
        assert "`BLOCKED`" in text
        assert "ordinary markers actually rendered" in text

    assert "TROUBLESHOOT_RECOVERED" in skill
    assert "TROUBLESHOOT_QUARANTINE" in skill
    assert "exact marker/artifact pair" in skill
    assert "terminal_options` renders its exact marker/artifact pair" in skill
    assert "rendered in `terminal_options` for the current attempt" in skill
    assert "ordinary markers rendered for this invocation" in skill
