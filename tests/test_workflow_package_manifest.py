from __future__ import annotations

import io
import json
import tarfile
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "millrace_workflow_package"
PACKAGE_ID = "millrace.plus.official"
PACKAGE_VERSION = "0.22.0"
WORKFLOW_SELECTORS = {
    ("simple_loop", "0.1"),
    ("execution.lad", "0.1"),
    ("execution.lad_integrator", "0.1"),
    ("planning.lad", "0.1"),
    ("lad.full", "0.1"),
    ("vendor_selection", "0.1"),
}

_ROOT_AUTHORITY_FIELDS = (
    "record_kind",
    "manifest_format_version",
    "package",
    "workflows",
    "assets",
    "dependencies",
    "compatibility",
    "canonicalization",
)
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


def _member_paths(manifest: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        sorted(
            (
                "manifest.json",
                *(str(asset["package_path"]) for asset in _assets(manifest)),
            )
        )
    )


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


def _manifest_digest(manifest: dict[str, Any]) -> str:
    canonical_bytes = json.dumps(
        _canonical_value(_manifest_mapping_authority(manifest)),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return (
        "sha256:" + sha256(_MANIFEST_DIGEST_DOMAIN_BYTES + canonical_bytes).hexdigest()
    )


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


def test_official_manifest_and_declared_assets_match_shipped_bytes() -> None:
    manifest = _load_manifest()
    package = manifest["package"]
    assert isinstance(package, dict)

    assert package["package_id"] == PACKAGE_ID
    assert package["package_version"] == PACKAGE_VERSION
    assert package["package_role"] == "workflow_package"
    assert manifest["manifest_digest"] == _manifest_digest(manifest)

    asset_digests = {
        str(asset["asset_id"]): str(asset["content_digest"])
        for asset in _assets(manifest)
    }
    assert len(asset_digests) == 62
    assert {asset["asset_kind"] for asset in _assets(manifest)} == {
        "entrypoint_prompt",
        "stage_skill",
    }
    for asset in _assets(manifest):
        package_path = str(asset["package_path"])
        asset_bytes = (PACKAGE_ROOT / package_path).read_bytes()
        assert asset["content_digest"] == _asset_digest(asset_bytes)
        assert asset["byte_length"] == len(asset_bytes)

    for workflow in _workflows(manifest):
        assert workflow["visibility"] == "public"
        assert workflow["entrypoints"] == ["default"]
        assert "assets" not in workflow["selected_authority"]
        for required_asset in workflow["required_assets"]:
            asset_id = str(required_asset["asset_id"])
            assert required_asset["content_digest"] == asset_digests[asset_id]


def test_public_workflow_package_declares_expected_workflows_only() -> None:
    manifest = _load_manifest()
    selectors = {
        (str(workflow["workflow_id"]), str(workflow["workflow_version"]))
        for workflow in _workflows(manifest)
    }

    assert selectors == WORKFLOW_SELECTORS
    assert manifest["dependencies"] == []


def test_package_paths_are_declared_contained_and_complete() -> None:
    manifest = _load_manifest()
    member_paths = _member_paths(manifest)

    assert member_paths == tuple(sorted(set(member_paths)))
    for member_path in member_paths:
        _assert_package_path(member_path)
        assert (PACKAGE_ROOT / member_path).is_file()

    declared_files = {
        path.relative_to(PACKAGE_ROOT).as_posix()
        for path in PACKAGE_ROOT.rglob("*")
        if path.is_file() and path.name != ".DS_Store"
    }
    assert declared_files == set(member_paths)


def test_public_archive_bytes_are_deterministic_and_data_only() -> None:
    manifest = _load_manifest()
    first_archive = _archive_bytes(manifest)
    second_archive = _archive_bytes(manifest)

    assert first_archive == second_archive
    with tarfile.open(fileobj=io.BytesIO(first_archive), mode="r:") as archive:
        members = archive.getmembers()
        names = tuple(sorted(member.name for member in members))
        payloads = {
            member.name: archive.extractfile(member).read()
            for member in members
            if archive.extractfile(member) is not None
        }

    assert names == _member_paths(manifest)
    assert not any(name.endswith((".py", ".pyc")) for name in names)
    assert payloads["manifest.json"] == (PACKAGE_ROOT / "manifest.json").read_bytes()
    for member in members:
        assert member.uid == 0
        assert member.gid == 0
        assert member.uname == ""
        assert member.gname == ""
        assert member.mtime == 0
        assert member.mode == 0o644
