from __future__ import annotations

import fnmatch
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "publish-to-pypi.yml"
WHEEL = "millrace_plus-0.22.0-py3-none-any.whl"
SDIST = "millrace_plus-0.22.0.tar.gz"
ACTION_PINS = {
    "actions/checkout": "df4cb1c069e1874edd31b4311f1884172cec0e10",
    "astral-sh/setup-uv": "37802adc94f370d6bfd71619e3f0bf239e1f3b78",
    "actions/upload-artifact": "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
    "actions/download-artifact": "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
    "pypa/gh-action-pypi-publish": "cef221092ed1bacb1cc03d23a2d87d1d172e277b",
}


def _workflow() -> str:
    assert WORKFLOW.is_file(), "publish-to-pypi.yml is required"
    return WORKFLOW.read_text(encoding="utf-8")


def _block(text: str, header: str) -> str:
    lines = text.splitlines()
    start = lines.index(header)
    indentation = len(header) - len(header.lstrip())
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if line.strip() and len(line) - len(line.lstrip()) <= indentation:
            end = index
            break
    return "\n".join(lines[start:end]).strip()


def _trigger_tags(workflow: str) -> tuple[str, ...]:
    trigger = _block(workflow, "on:")
    lines = trigger.splitlines()
    tags = lines.index("    tags:")
    return tuple(
        line.removeprefix("      - ")
        for line in lines[tags + 1 :]
        if line.startswith("      - ")
    )


def _accepts_ref(workflow: str, ref: str) -> bool:
    prefix = "refs/tags/"
    if not ref.startswith(prefix):
        return False
    tag = ref.removeprefix(prefix)
    trigger_accepts = any(
        fnmatch.fnmatchcase(tag, pattern) for pattern in _trigger_tags(workflow)
    )
    publish_job = _block(workflow, "  publish:")
    guards = re.findall(r"github\.ref == '([^']+)'", publish_job)
    return trigger_accepts and guards == [ref]


def _sha256_manifest(block: str) -> tuple[str, ...]:
    lines = block.splitlines()
    start = lines.index("          sha256sum --check --strict <<'SHA256'") + 1
    end = lines.index("          SHA256", start)
    return tuple(lines[start:end])


def _expected_files(block: str) -> tuple[str, ...]:
    lines = block.splitlines()
    start = lines.index("          expected=(") + 1
    end = lines.index("          )", start)
    return tuple(
        line.strip().removeprefix('"').removesuffix('"')
        for line in lines[start:end]
    )


def test_release_workflow_accepts_only_exact_project_version_tag() -> None:
    workflow = _workflow()
    with (ROOT / "pyproject.toml").open("rb") as source:
        version = tomllib.load(source)["project"]["version"]
    expected_ref = f"refs/tags/v{version}"

    assert version == "0.22.0"
    assert _trigger_tags(workflow) == (f"v{version}",)
    cases = {
        expected_ref: True,
        f"refs/tags/{version}": False,
        f"refs/tags/release-v{version}": False,
        f"{expected_ref}-extra": False,
        "refs/tags/v0.22.1": False,
        f"{expected_ref}rc1": False,
    }
    assert {ref: _accepts_ref(workflow, ref) for ref in cases} == cases


def test_release_workflow_validates_exact_built_distributions_before_publish() -> (
    None
):
    workflow = _workflow()
    validate_job = _block(workflow, "  validate:")
    publish_job = _block(workflow, "  publish:")

    checkout = validate_job.index(
        f"uses: actions/checkout@{ACTION_PINS['actions/checkout']}"
    )
    setup_uv = validate_job.index(
        f"uses: astral-sh/setup-uv@{ACTION_PINS['astral-sh/setup-uv']}"
    )
    tests = validate_job.index("- name: Test")
    lint = validate_job.index("- name: Lint")
    clean_source = validate_job.index("- name: Check clean source tree")
    build = validate_job.index("- name: Build")
    verify = validate_job.index("- name: Verify built distributions")
    clean = validate_job.index("- name: Check source unchanged")
    upload = validate_job.index(
        f"uses: actions/upload-artifact@{ACTION_PINS['actions/upload-artifact']}"
    )
    assert (
        checkout
        < setup_uv
        < tests
        < lint
        < clean_source
        < build
        < verify
        < clean
        < upload
    )
    assert "persist-credentials: false" in validate_job[checkout:setup_uv]
    assert "pytest==9.1.1" in validate_job[tests:lint]
    assert "pytest -q" in validate_job[tests:lint]
    assert "ruff==0.15.21" in validate_job[lint:clean_source]
    assert "ruff check src tests" in validate_job[lint:clean_source]
    assert (
        'test -z "$(git status --porcelain=v1 --untracked-files=all)"'
        in validate_job[clean_source:build]
    )
    build_step = validate_job[build:verify]
    absent_dist = build_step.index("test ! -e dist")
    fixed_epoch = build_step.index("SOURCE_DATE_EPOCH=1580601600")
    build_command = build_step.index("uv build --python 3.11 --force-pep517")
    assert absent_dist < fixed_epoch < build_command
    assert "rm -rf dist" not in workflow

    build_verify = validate_job[verify:clean]
    assert _expected_files(build_verify) == (".gitignore", WHEEL, SDIST)
    assert "find dist -maxdepth 1 -type f -printf '%f\\n' | sort" in build_verify
    assert 'if [[ "${actual[*]}" != "${expected_sorted[*]}" ]]; then' in build_verify
    assert "unexpected distribution set" in build_verify
    assert "exit 1" in build_verify
    built_hashes = _sha256_manifest(build_verify)
    assert len(built_hashes) == 2
    assert all(
        re.fullmatch(r"          [0-9a-f]{64}  dist/\S+", line)
        for line in built_hashes
    )
    assert [line.split()[-1] for line in built_hashes] == [
        f"dist/{WHEEL}",
        f"dist/{SDIST}",
    ]
    assert "git diff --exit-code" in validate_job[clean:upload]

    upload_block = validate_job[upload:]
    assert "name: pypi-distributions" in upload_block
    assert f"dist/{WHEEL}" in upload_block
    assert f"dist/{SDIST}" in upload_block
    assert "path: dist/" not in upload_block
    assert "if-no-files-found: error" in upload_block
    assert _block(upload_block, "          path: |") == "\n".join(
        ["path: |", f"            dist/{WHEEL}", f"            dist/{SDIST}"]
    )

    download = publish_job.index(
        f"uses: actions/download-artifact@{ACTION_PINS['actions/download-artifact']}"
    )
    recheck = publish_job.index("- name: Verify downloaded distributions")
    publish = publish_job.index(
        "uses: pypa/gh-action-pypi-publish@"
        f"{ACTION_PINS['pypa/gh-action-pypi-publish']}"
    )
    assert download < recheck < publish
    assert "name: pypi-distributions" in publish_job[download:recheck]
    assert "path: dist/" in publish_job[download:recheck]
    publish_verify = publish_job[recheck:publish]
    assert _expected_files(publish_verify) == (WHEEL, SDIST)
    assert "find dist -maxdepth 1 -type f -printf '%f\\n' | sort" in publish_verify
    assert 'if [[ "${actual[*]}" != "${expected_sorted[*]}" ]]; then' in (
        publish_verify
    )
    assert "unexpected downloaded distribution set" in publish_verify
    assert "exit 1" in publish_verify
    downloaded_hashes = _sha256_manifest(publish_verify)
    assert downloaded_hashes == built_hashes
    assert workflow.count("sha256sum --check --strict <<'SHA256'") == 2
    assert "packages-dir: dist/" in publish_job[publish:]
    assert workflow.count("uses: pypa/gh-action-pypi-publish@") == 1


def test_release_workflow_uses_oidc_and_immutable_actions_only() -> None:
    workflow = _workflow()
    lowered = workflow.lower()
    header = workflow[: workflow.index("jobs:")]
    validate_job = _block(workflow, "  validate:")
    publish_job = _block(workflow, "  publish:")

    assert "permissions:" not in header
    assert _block(validate_job, "    permissions:") == "\n".join(
        ["permissions:", "      contents: read"]
    )
    assert _block(publish_job, "    environment:") == "\n".join(
        ["environment:", "      name: pypi"]
    )
    assert _block(publish_job, "    permissions:") == "\n".join(
        ["permissions:", "      contents: read", "      id-token: write"]
    )
    assert "id-token: write" not in validate_job
    assert workflow.count("id-token: write") == 1
    assert "needs: validate" in publish_job

    for forbidden in (
        "branches:",
        "pull_request:",
        "release:",
        "repository_dispatch:",
        "schedule:",
        "workflow_call:",
        "workflow_dispatch:",
        "skip-existing",
        "secrets.",
        "api-token",
        "pypi_api_token",
        "password:",
        "repository-url:",
    ):
        assert forbidden not in lowered
    without_oidc_permission = lowered.replace("id-token: write", "")
    assert not re.search(
        r"(?:^|[^a-z])(?:api[-_]?token|pypi[-_]?token|token)(?:[^a-z]|$)",
        without_oidc_permission,
    )

    uses = re.findall(r"^\s*-\s+uses:\s*([^\s]+)$", workflow, re.MULTILINE)
    expected = {f"{action}@{commit}" for action, commit in ACTION_PINS.items()}
    assert set(uses) == expected
    assert len(uses) == len(expected)
    assert all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", use) for use in uses)
