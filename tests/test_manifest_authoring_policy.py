from __future__ import annotations

import io
import json
import re
import tarfile
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "millrace_workflow_package"
POLICY_DOC = PROJECT_ROOT / "docs" / "manifest-authoring-policy.md"

_ROOT_ENVELOPE_FIELDS = (
    "record_kind",
    "manifest_format_version",
    "package",
    "workflows",
    "assets",
    "dependencies",
    "compatibility",
    "canonicalization",
    "manifest_digest",
    "non_authoritative_metadata",
)
_ROOT_AUTHORITY_FIELDS = _ROOT_ENVELOPE_FIELDS[:-2]
_PACKAGE_PROVENANCE_FIELDS = frozenset(
    {
        "source_kind",
        "publication_scope",
        "license",
        "repository_url",
        "source_ref",
        "display",
    }
)
_MANIFEST_DIGEST_DOMAIN_BYTES = b"millrace.wpkg.manifest.v1\0"
_ASSET_DIGEST_DOMAIN_BYTES = b"millrace.wpkg.asset.v1\0"
_PACKAGE_DIGEST_DOMAIN_BYTES = b"millrace.wpkg.archive.v1\0"
_WORKFLOW_FREEZE_DOMAIN_BYTES = b"millrace.plus.workflow.freeze.v1\0"
RELEASE_IDENTITY = "0.22.0"
_EVIDENCE_PATTERN = re.compile(
    r"<!-- manifest-freeze-evidence:BEGIN -->\n"
    r"```json\n"
    r"(?P<json>.*?)\n"
    r"```\n"
    r"<!-- manifest-freeze-evidence:END -->",
    re.DOTALL,
)


def _load_manifest() -> dict[str, Any]:
    return json.loads((PACKAGE_ROOT / "manifest.json").read_text())


def _assets(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    assets = manifest["assets"]
    assert isinstance(assets, list)
    return assets


def _workflows(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    workflows = manifest["workflows"]
    assert isinstance(workflows, list)
    return workflows


def _asset_pins(manifest: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "asset_id": str(asset["asset_id"]),
            "content_digest": str(asset["content_digest"]),
            "package_path": str(asset["package_path"]),
        }
        for asset in sorted(_assets(manifest), key=lambda record: record["asset_id"])
    ]


def _selected_package_pin(manifest: dict[str, Any]) -> dict[str, str]:
    package = manifest["package"]
    return {
        "package_id": str(package["package_id"]),
        "package_version": str(package["package_version"]),
        "package_format_version": str(package["package_format_version"]),
    }


def _policy_evidence() -> dict[str, Any]:
    assert POLICY_DOC.is_file(), f"missing manifest policy doc: {POLICY_DOC}"
    match = _EVIDENCE_PATTERN.search(POLICY_DOC.read_text())
    assert match is not None, "missing manifest freeze evidence block"
    evidence = json.loads(match.group("json"))
    assert isinstance(evidence, dict)
    return evidence


def _manifest_digest(manifest: dict[str, Any]) -> str:
    return "sha256:" + sha256(
        _MANIFEST_DIGEST_DOMAIN_BYTES
        + _canonical_bytes(_manifest_mapping_authority(manifest))
    ).hexdigest()


def _manifest_mapping_authority(manifest: dict[str, Any]) -> dict[str, Any]:
    authority: dict[str, Any] = {}
    for field in _ROOT_AUTHORITY_FIELDS:
        if field not in manifest:
            continue
        value = manifest[field]
        if field == "package":
            authority[field] = _package_mapping_authority(value)
        elif field == "workflows":
            authority[field] = _sorted_mapping_records(
                value,
                "workflow_id",
                normalizer=_workflow_mapping_authority,
            )
        elif field == "assets":
            authority[field] = _sorted_mapping_records(value, "asset_id")
        elif field == "dependencies":
            authority[field] = _sorted_mapping_records(
                value,
                "package_id",
                "version_constraint",
                normalizer=_optional_none_mapping_authority,
            )
        else:
            authority[field] = value
    return authority


def _package_mapping_authority(value: object) -> object:
    if not isinstance(value, dict):
        return value
    return {
        key: nested_value
        for key, nested_value in value.items()
        if key not in _PACKAGE_PROVENANCE_FIELDS
    }


def _workflow_mapping_authority(value: object) -> object:
    if not isinstance(value, dict):
        return value
    record = {
        key: nested_value
        for key, nested_value in value.items()
        if key not in {"display", "source_refs"}
    }
    if "required_assets" in record:
        record["required_assets"] = _sorted_mapping_records(
            record["required_assets"],
            "asset_id",
            normalizer=_optional_none_mapping_authority,
        )
    if not record.get("required_dependencies"):
        record.pop("required_dependencies", None)
    return record


def _optional_none_mapping_authority(value: object) -> object:
    if not isinstance(value, dict):
        return value
    return {
        key: nested_value
        for key, nested_value in value.items()
        if nested_value is not None
    }


def _sorted_mapping_records(
    value: object,
    *key_fields: str,
    normalizer: Any = None,
) -> object:
    if not isinstance(value, (list, tuple)):
        return value
    records = [
        normalizer(record) if normalizer is not None else record for record in value
    ]

    def sort_key(record: object) -> tuple[str, ...]:
        if not isinstance(record, dict):
            return ("",)
        return tuple(str(record.get(field, "")) for field in key_fields)

    return sorted(records, key=sort_key)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        _canonical_value(value),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _canonical_value(value: object) -> object:
    if value is None or isinstance(value, (str, bool)):
        return value
    if type(value) is int:
        return value
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _canonical_value(nested) for key, nested in value.items()}
    raise AssertionError(f"unsupported manifest value: {type(value).__name__}")


def _package_digest(manifest: dict[str, Any]) -> str:
    return "sha256:" + sha256(
        _PACKAGE_DIGEST_DOMAIN_BYTES + _archive_bytes(manifest)
    ).hexdigest()


def _archive_bytes(manifest: dict[str, Any]) -> bytes:
    members = [
        ("manifest.json", (PACKAGE_ROOT / "manifest.json").read_bytes()),
        *(
            (
                str(asset["package_path"]),
                (PACKAGE_ROOT / str(asset["package_path"])).read_bytes(),
            )
            for asset in _assets(manifest)
        ),
    ]
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for package_path, payload in sorted(members, key=lambda item: item[0]):
            _assert_package_path(package_path)
            info = tarfile.TarInfo(package_path)
            info.size = len(payload)
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mtime = 0
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(payload))
    return stream.getvalue()


def _assert_package_path(package_path: str) -> None:
    path = PurePosixPath(package_path)
    assert package_path
    assert not path.is_absolute()
    assert "\\" not in package_path
    assert "" not in path.parts
    assert "." not in path.parts
    assert ".." not in path.parts
    assert ".DS_Store" not in path.parts


def _asset_digest(asset_bytes: bytes) -> str:
    return "sha256:" + sha256(_ASSET_DIGEST_DOMAIN_BYTES + asset_bytes).hexdigest()


def _workflow_freeze_fingerprints(manifest: dict[str, Any]) -> dict[str, str]:
    return {
        f"{workflow['workflow_id']}@{workflow['workflow_version']}": (
            _workflow_freeze_fingerprint(manifest, workflow)
        )
        for workflow in sorted(
            _workflows(manifest),
            key=lambda record: (record["workflow_id"], record["workflow_version"]),
        )
    }


def _workflow_freeze_fingerprint(
    manifest: dict[str, Any],
    workflow: dict[str, Any],
) -> str:
    assets_by_id = {str(asset["asset_id"]): asset for asset in _assets(manifest)}
    selected_asset_pins = []
    for required_asset in workflow["required_assets"]:
        asset_id = str(required_asset["asset_id"])
        selected_asset_pins.append(
            {
                "asset_id": asset_id,
                "content_digest": str(required_asset["content_digest"]),
                "package_path": str(assets_by_id[asset_id]["package_path"]),
            }
        )

    payload = {
        "selected_package_pin": _selected_package_pin(manifest),
        "workflow_id": str(workflow["workflow_id"]),
        "workflow_version": str(workflow["workflow_version"]),
        "entrypoints": workflow["entrypoints"],
        "selected_authority": workflow["selected_authority"],
        "selected_asset_pins": sorted(
            selected_asset_pins,
            key=lambda record: record["asset_id"],
        ),
        "selected_dependency_pins": workflow.get("required_dependencies", []),
    }
    return "sha256:" + sha256(
        _WORKFLOW_FREEZE_DOMAIN_BYTES + _canonical_bytes(payload)
    ).hexdigest()


def _expected_policy_evidence() -> dict[str, object]:
    manifest = _load_manifest()
    for asset in _assets(manifest):
        package_path = str(asset["package_path"])
        asset_bytes = (PACKAGE_ROOT / package_path).read_bytes()
        assert asset["content_digest"] == _asset_digest(asset_bytes)
        assert asset["byte_length"] == len(asset_bytes)

    return {
        "policy": "frozen-manifest",
        "manifest_digest": _manifest_digest(manifest),
        "package_digest": _package_digest(manifest),
        "selected_package_pin": _selected_package_pin(manifest),
        "selected_workflow_fingerprints": _workflow_freeze_fingerprints(manifest),
        "asset_pins": _asset_pins(manifest),
    }


def test_manifest_authoring_policy_declares_frozen_source_of_truth() -> None:
    assert POLICY_DOC.is_file(), f"missing manifest policy doc: {POLICY_DOC}"
    policy = POLICY_DOC.read_text()
    policy_search_text = " ".join(policy.split())
    readme = (PROJECT_ROOT / "README.md").read_text()
    public_validation = (PROJECT_ROOT / "docs" / "public-validation.md").read_text()

    for required in (
        "`millrace_workflow_package/manifest.json` is the committed source of truth",
        "canonical JSON rather than generated output",
        "Standalone validation does not need donor workflow functions",
        "`tests/test_manifest_authoring_policy.py` recomputes",
        "manifest digest",
        "package digest",
        "selected workflow fingerprints",
        "asset pins",
    ):
        assert required in policy_search_text

    assert "docs/manifest-authoring-policy.md" in readme
    assert "tests/test_manifest_authoring_policy.py" in public_validation


def test_manifest_freeze_evidence_matches_current_package_bytes() -> None:
    assert _policy_evidence() == _expected_policy_evidence()


def test_manifest_json_uses_canonical_authoring_format() -> None:
    manifest_path = PACKAGE_ROOT / "manifest.json"
    raw_manifest = manifest_path.read_text()
    manifest = json.loads(raw_manifest)

    assert tuple(manifest) == _ROOT_ENVELOPE_FIELDS
    assert manifest["manifest_digest"] == _manifest_digest(manifest)
    assert raw_manifest == json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"


def test_release_identity_is_covered_by_canonical_manifest_digest() -> None:
    manifest = _load_manifest()
    package = manifest["package"]
    assert isinstance(package, dict)
    assert package["package_version"] == RELEASE_IDENTITY

    changed_identity = json.loads(json.dumps(manifest))
    changed_package = changed_identity["package"]
    assert isinstance(changed_package, dict)
    changed_package["package_version"] = "0.22.1"

    assert _manifest_digest(manifest) != _manifest_digest(changed_identity)


def test_vendor_selection_examples_use_selected_plan_identity() -> None:
    skills_root = PACKAGE_ROOT / "assets/workflows/vendor_selection/skills"
    selected_text = "\n".join(
        path.read_text() for path in sorted(skills_root.glob("*-core.md"))
    )

    assert "selected-plan-e2e-vendor-selection" not in selected_text
    selected_plan_ids = re.findall(
        r'"selected_plan_id": "([^"]+)"',
        selected_text,
    )
    assert selected_plan_ids
    assert set(selected_plan_ids) == {"vendor_selection:0.1"}
    assert '"selected_plan_id"' not in (
        skills_root / "policy_screener-core.md"
    ).read_text()
