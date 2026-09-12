from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PROJECT_ROOT / "src" / "millrace_plus" / "skills"
WORKFLOW_MANIFEST = PROJECT_ROOT / "millrace_workflow_package" / "manifest.json"

FROZEN_SKILL_DIGESTS = dict(
    (
        (
            "millrace-entrypoint-authoring/SKILL.md",
            "423d5f7b8d3c55bf27b9d9e39de6fedcd874570148e8c5cbe0ccc0c604884dbc",
        ),
        (
            "millrace-entrypoint-authoring/agents/openai.yaml",
            "1adb9656c4f524711b8eed58695e712f4163733e58fec5972f0c42cedb0aa20a",
        ),
        (
            "millrace-entrypoint-authoring/references/artifact-handoff-patterns.md",
            "6a7e369084f6935a7e446b20339d6386da2e53b4c0fa9276a01df8f6503bdfac",
        ),
        (
            "millrace-entrypoint-authoring/references/core-entrypoint-skill-pattern.md",
            "cb1445d369d9cf3a0a71f9f43fb3c462cb08abaafcf39487bfa62737382007bc",
        ),
        (
            "millrace-entrypoint-authoring/references/dispatch-and-evidence-boundaries.md",
            "e968f63eccb4b7062ead500b118f5e8005d60dcee470e2f0994f81c18f378b8d",
        ),
        (
            "millrace-entrypoint-authoring/references/entrypoint-prompt-pattern.md",
            "09c2f784d0bb7d62d855ecd4aff275c4e1c9989e8df7f15a668edad0a1446a7f",
        ),
        (
            "millrace-entrypoint-authoring/references/entrypoint-review-checklist.md",
            "ea478f005dd6549ce0d241e0d48f50761a77ecc753e360e8aca79c14469c2129",
        ),
        (
            "millrace-entrypoint-authoring/references/terminal-markers-and-runtime-authority.md",
            "c4f8fcc7adc844a80617da476e3da7623c29526415e0c0c9a3e6734c8364777f",
        ),
        (
            "millrace-entrypoint-authoring/references/worked-examples.md",
            "4b544b837de5ec4353b8374de690ef61a1090f2b4d67d6e1671d746c1312c9c4",
        ),
        (
            "millrace-instruction-manual/SKILL.md",
            "647ffaf3f623cd12248a55d6b7ec8c275a107af79cea6b854163c474560b0e4f",
        ),
        (
            "millrace-instruction-manual/agents/openai.yaml",
            "c8fb067f1af3ada8bdcb895969397484a49038c5d75e00d0d3a210ecf8b00e08",
        ),
        (
            "millrace-instruction-manual/references/cli-operations.md",
            "c79ae3d463c25846a483fa51e1076cf33323e43c01176698f1598101bdeb37d6",
        ),
        (
            "millrace-instruction-manual/references/current-capabilities.md",
            "53323cc425bdc0a9c9e489f2646ff1112995ce63b4c38d03875531c7b79868fc",
        ),
        (
            "millrace-instruction-manual/references/install-and-deploy.md",
            "c9c88e4f3d2e0bfebbf34165d5540e350d8db128f4754d17b0ca62ad158e171c",
        ),
        (
            "millrace-loop-configuration/SKILL.md",
            "24c503f0aee35ceab3e3a0de67bf8ec1b101b89d89e578273a9307e318c69d0e",
        ),
        (
            "millrace-loop-configuration/agents/openai.yaml",
            "1d388667fa8c4c97522fa96a9bb95ce9bc0c07fdbfd5f36cdea7190138f59517",
        ),
        (
            "millrace-loop-configuration/references/decision-tree-design.md",
            "fa46599617072da11b766729bc17682b5d96c6bfa930bd63dc623fc1d84cee7c",
        ),
        (
            "millrace-loop-configuration/references/planes-and-compilation.md",
            "a8864cc28abaf1b38d438c4f59fc8e530d020518ed1f4c3aeb110ea34460f3fc",
        ),
        (
            "millrace-loop-configuration/references/worked-examples.md",
            "eec33551537c68e0579b2481a98965d9791a57eace6f5eb9bf60d58637b83e69",
        ),
        (
            "millrace-loop-configuration/references/workflow-author-contract.md",
            "cef911395d1474bb846576c818acf8c12307e44dd2faed1dcba89f829694cb45",
        ),
    )
)


def test_agent_skill_inventory_and_bytes_match_frozen_donor() -> None:
    files = {
        path.relative_to(SKILL_ROOT).as_posix(): path
        for path in SKILL_ROOT.rglob("*")
        if path.is_file() and not path.is_symlink()
    }

    assert set(files) == set(FROZEN_SKILL_DIGESTS)
    assert {Path(relative_path).parts[0] for relative_path in files} == {
        "millrace-entrypoint-authoring",
        "millrace-instruction-manual",
        "millrace-loop-configuration",
    }
    for relative_path, expected_digest in FROZEN_SKILL_DIGESTS.items():
        assert sha256(files[relative_path].read_bytes()).hexdigest() == expected_digest


def test_agent_skill_guidance_covers_terminal_schema_and_recovery_boundaries() -> None:
    entrypoint = (SKILL_ROOT / "millrace-entrypoint-authoring/SKILL.md").read_text()
    loop = (SKILL_ROOT / "millrace-loop-configuration/SKILL.md").read_text()
    combined = " ".join(f"{entrypoint}\n{loop}".split())

    for required in (
        "every legal terminal branch",
        "exact marker-to-selected-schema/null handoff contract",
        "runner dispatch must project exact selected terminal schema material",
        "Examples must cover every branch",
        "schema-invalid shapes",
        "Runner output and rejected-result evidence are non-authoritative",
        "selected workflow authority",
        "not inferred from a folder or prompt prose",
        "advisory package data",
        "not runtime-installed authority",
    ):
        assert required in combined


def test_new_generic_guidance_has_no_workflow_specific_coupling() -> None:
    entrypoint = (SKILL_ROOT / "millrace-entrypoint-authoring/SKILL.md").read_text()
    loop = (SKILL_ROOT / "millrace-loop-configuration/SKILL.md").read_text()
    generic_sections = "\n".join(
        (
            entrypoint.split("## Selected Terminal Handoff Rules", maxsplit=1)[1]
            .split("\n## ", maxsplit=1)[0],
            loop.split("## Selected Terminal And Recovery Contracts", maxsplit=1)[1]
            .split("\n## ", maxsplit=1)[0],
        )
    ).lower()

    for forbidden_term in (
        "recon",
        "lad",
        "luna",
        "e2e",
        "macos",
        "planning",
        "execution",
        "probe",
        "task",
        "spec",
    ):
        assert forbidden_term not in generic_sections


def test_agent_skills_are_not_workflow_manifest_authority() -> None:
    manifest_text = WORKFLOW_MANIFEST.read_text()
    manifest = json.loads(manifest_text)

    assert all(
        skill_name not in manifest_text
        for skill_name in (
            "millrace-entrypoint-authoring",
            "millrace-instruction-manual",
            "millrace-loop-configuration",
        )
    )
    assert all(
        not str(asset["package_path"]).startswith("millrace_plus/skills/")
        for asset in manifest["assets"]
    )
