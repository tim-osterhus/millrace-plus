"""Local graph folders and independent copies, without a runtime dependency."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import stat
import tempfile
from pathlib import Path
from typing import TypedDict

_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")


class CatalogError(Exception):
    """A catalog/ownership refusal with a stable machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class Template(TypedDict):
    selector: str
    name: str
    source: str
    path: str


def bundled_root() -> Path:
    return Path(__file__).with_name("workflows")


def default_catalog() -> Path:
    return Path.home() / ".local/share/millrace-plus/workflows"


def _canonical(path: Path, *, expand_user: bool = True) -> Path:
    try:
        return (path.expanduser() if expand_user else path).resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise CatalogError(
            "io_error", f"Cannot resolve catalog path {path}: {exc}"
        ) from exc


def catalog_settings_path() -> Path:
    return Path.home() / ".config/millrace-plus/catalog.json"


def _saved_catalog() -> Path | None:
    settings = catalog_settings_path()
    try:
        raw = settings.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise CatalogError("io_error", f"Cannot read {settings}: {exc}") from exc
    try:
        value = strict_json(raw)
    except CatalogError as exc:
        raise CatalogError(
            "invalid_catalog_settings", f"Invalid {settings}: {exc}"
        ) from exc
    if (
        not isinstance(value, dict)
        or set(value) != {"catalog_home"}
        or not isinstance(value["catalog_home"], str)
        or not value["catalog_home"]
        or not Path(value["catalog_home"]).is_absolute()
    ):
        raise CatalogError(
            "invalid_catalog_settings",
            f"Expected an absolute catalog_home in {settings}",
        )
    return _canonical(Path(value["catalog_home"]))


def _selected_catalog(catalog_home: Path | None = None) -> Path:
    return (
        catalog_home
        if catalog_home is not None
        else _saved_catalog() or default_catalog()
    )


def catalog_home_status() -> dict[str, str]:
    """Read the saved user default, or the ordinary fallback, without writing."""
    saved = _saved_catalog()
    return {
        "catalog_home": str(_canonical(saved or default_catalog())),
        "source": "saved" if saved is not None else "default",
        "settings_file": str(catalog_settings_path()),
    }


def set_catalog_home(path: Path) -> dict[str, str]:
    """Save an existing catalog directory as this user's default atomically."""
    selected = _canonical(path)
    if not selected.is_dir():
        raise CatalogError(
            "invalid_catalog_root",
            f"Catalog root must be an existing directory: {selected}",
        )
    settings = catalog_settings_path()
    temporary: Path | None = None
    try:
        settings.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=settings.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps({"catalog_home": str(selected)}) + "\n")
        temporary.replace(settings)
    except OSError as exc:
        raise CatalogError("io_error", f"Cannot save {settings}: {exc}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return catalog_home_status()


def reset_catalog_home() -> dict[str, str]:
    """Remove the saved default; leave all catalog folders and templates intact."""
    settings = catalog_settings_path()
    try:
        settings.unlink(missing_ok=True)
    except OSError as exc:
        raise CatalogError("io_error", f"Cannot reset {settings}: {exc}") from exc
    return catalog_home_status()


def known_roots(catalog_home: Path | None = None) -> tuple[Path, ...]:
    saved = _saved_catalog()
    return tuple(
        dict.fromkeys(
            _canonical(p)
            for p in (
                bundled_root(),
                default_catalog(),
                *([saved] if saved is not None else []),
                catalog_home or default_catalog(),
            )
        )
    )


def _within(path: Path, root: Path) -> bool:
    if path == root or root in path.parents:
        return True
    # samefile also catches filesystem aliases whose spelling differs (Darwin).
    for ancestor in (path, *path.parents):
        try:
            if ancestor.samefile(root):
                return True
        except OSError:
            continue
    return False


def check_ownership(
    workflow_file: Path, project_root: Path, *, catalog_home: Path | None = None
) -> None:
    """Refuse shared workflow/project locations; this is not a sandbox."""
    for path in (
        _canonical(workflow_file, expand_user=False).parent,
        _canonical(project_root, expand_user=False),
    ):
        if any(_within(path, root) for root in known_roots(catalog_home)):
            raise CatalogError(
                "catalog_ownership",
                f"Use independent workflow/project roots outside catalogs: {path}",
            )


def strict_json(raw: str | bytes) -> object:
    """Decode ordinary strict JSON, rejecting duplicate keys and nonfinite values."""

    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate key: {key}")
            result[key] = value
        return result

    def constant(value: str) -> object:
        raise ValueError(f"nonfinite number: {value}")

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise CatalogError("invalid_template", f"Invalid strict JSON: {exc}") from exc


def list_templates(*, catalog_home: Path | None = None) -> list[Template]:
    """List qualified immediate folder entries; malformed candidates fail the list."""
    entries: list[Template] = []
    for source, root in (
        ("bundled", bundled_root()),
        ("local", _selected_catalog(catalog_home)),
    ):
        root = _canonical(root)
        if not root.exists():
            continue
        if not root.is_dir():
            raise CatalogError(
                "invalid_catalog_root", f"Catalog root must be a directory: {root}"
            )
        try:
            for child in sorted(root.iterdir(), key=lambda p: p.name):
                if not _NAME.fullmatch(child.name):
                    continue
                if child.is_symlink():
                    raise CatalogError(
                        "invalid_template", f"Linked template candidate: {child}"
                    )
                if not child.is_dir():
                    continue
                graph = child / "workflow.json"
                if not os.path.lexists(graph):
                    continue
                if graph.is_symlink() or not graph.is_file():
                    raise CatalogError(
                        "invalid_template", f"Invalid workflow.json: {graph}"
                    )
                entries.append(
                    {
                        "selector": f"{source}:{child.name}",
                        "name": child.name,
                        "source": source,
                        "path": str(child),
                    }
                )
        except OSError as exc:
            raise CatalogError(
                "io_error", f"Cannot list catalog {root}: {exc}"
            ) from exc
    return entries


def _source_files(source: Path) -> list[tuple[Path, bool]]:
    if source.is_symlink() or not source.is_dir():
        raise CatalogError(
            "invalid_template",
            f"Workflow source must be an ordinary directory: {source}",
        )
    files: list[tuple[Path, bool]] = []

    def visit(folder: Path) -> None:
        for path in sorted(folder.iterdir()):
            if path.name == "__pycache__":
                continue
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode):
                files.append((path.relative_to(source), True))
                visit(path)
            elif stat.S_ISREG(mode):
                files.append((path.relative_to(source), False))
            else:
                raise CatalogError(
                    "unsupported_file_type",
                    f"Only regular files/directories may be copied: {path}",
                )

    visit(source)
    graph = source / "workflow.json"
    if not any(
        name == Path("workflow.json") and not directory for name, directory in files
    ):
        raise CatalogError(
            "invalid_template", f"Required graph-v3 workflow.json missing: {source}"
        )
    value = strict_json(graph.read_bytes())
    if not isinstance(value, dict) or value.get("format") != "millrace-router/v3":
        raise CatalogError(
            "invalid_template", f"Reauthor this folder as millrace-router/v3: {graph}"
        )
    controls = value.get("control_files", [])
    if isinstance(controls, list) and any(
        isinstance(p, str) and "__pycache__" in Path(p).parts for p in controls
    ):
        raise CatalogError(
            "invalid_template", f"Controls inside __pycache__ are unsupported: {graph}"
        )
    return files


def _fingerprint_files(source: Path, files: list[tuple[Path, bool]]) -> str:
    records: list[dict[str, str | int]] = []
    for relative, directory in files:
        if directory:
            continue
        path = source / relative
        try:
            mode = path.lstat().st_mode
            if not stat.S_ISREG(mode):
                raise CatalogError(
                    "unsupported_file_type",
                    f"Only regular files may be fingerprinted: {path}",
                )
            content = path.read_bytes()
        except OSError as exc:
            raise CatalogError(
                "io_error", f"Cannot read workflow file {path}: {exc}"
            ) from exc
        records.append(
            {
                "path": relative.as_posix(),
                "executable_bits": mode & 0o111,
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    records.sort(key=lambda record: str(record["path"]))
    encoded = json.dumps(
        records, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def workflow_fingerprint(source: Path) -> str:
    """Return the SHA-256 definition digest for an ordinary workflow folder.

    Raises:
        CatalogError: The source is invalid or contains a linked/special file.
    """
    try:
        return _fingerprint_files(source, _source_files(source))
    except CatalogError:
        raise
    except (OSError, RuntimeError, UnicodeError, ValueError) as exc:
        raise CatalogError(
            "io_error", f"Cannot fingerprint workflow source {source}: {exc}"
        ) from exc


def _plus_version() -> str:
    try:
        return importlib.metadata.version("millrace-plus")
    except importlib.metadata.PackageNotFoundError:
        from millrace_plus import __version__

        return __version__


def _copy(
    source: Path, destination: Path, *, catalog_home: Path | None, into_catalog: bool
) -> dict[str, str]:
    owned = False
    try:
        source = source.expanduser().absolute()
        destination = destination.expanduser().absolute()
        if os.path.lexists(destination):
            raise CatalogError(
                "destination_exists", f"Destination already exists: {destination}"
            )
        canonical_source, canonical_dest = _canonical(source), _canonical(destination)
        local = _canonical(_selected_catalog(catalog_home))
        if _within(canonical_dest, canonical_source) or _within(
            canonical_source, canonical_dest
        ):
            raise CatalogError(
                "forbidden_destination",
                "Source and destination cannot contain one another",
            )
        local_child = (
            into_catalog
            and canonical_dest.parent == local
            and bool(_NAME.fullmatch(canonical_dest.name))
        )
        if _within(canonical_dest, _canonical(bundled_root())):
            raise CatalogError(
                "forbidden_destination", "Cannot copy into bundled assets"
            )
        for root in known_roots(catalog_home):
            allowed_local_child = local_child and _within(local, root)
            if _within(canonical_dest, root) and not allowed_local_child:
                raise CatalogError(
                    "forbidden_destination",
                    f"Use a new workspace or immediate catalog child: {destination}",
                )
        if not destination.parent.is_dir():
            raise CatalogError(
                "destination_parent_missing",
                f"Create the parent/catalog directory first: {destination.parent}",
            )
        files = _source_files(source)
        source_digest = _fingerprint_files(source, files)
        destination.mkdir()
        owned = True
        for relative, directory in files:
            target = destination / relative
            if directory:
                target.mkdir()
            else:
                shutil.copy2(source / relative, target)
        if (
            workflow_fingerprint(source) != source_digest
            or workflow_fingerprint(destination) != source_digest
        ):
            raise CatalogError(
                "source_changed",
                f"Workflow source changed during copy: {source}",
            )
        return {"source": str(canonical_source), "destination": str(canonical_dest)}
    except CatalogError:
        if owned:
            shutil.rmtree(destination)
        raise
    except FileExistsError as exc:
        if owned:
            shutil.rmtree(destination)
        raise CatalogError(
            "destination_exists", f"Destination already exists: {destination}"
        ) from exc
    except (OSError, RuntimeError, ValueError) as exc:
        if owned:
            shutil.rmtree(destination)
        raise CatalogError("io_error", f"Cannot copy workflow: {exc}") from exc


def copy_workflow(
    source: Path, destination: Path, *, catalog_home: Path | None = None
) -> dict[str, str]:
    """Copy an ordinary folder into a fresh workspace or immediate catalog child.

    Raises CatalogError; a failed copy removes only its exclusively owned root.
    Concurrent source/destination mutation is unsupported.
    """
    return _copy(source, destination, catalog_home=catalog_home, into_catalog=True)


def init_template(
    selector: str, destination: Path, *, catalog_home: Path | None = None
) -> dict[str, object]:
    """Initialize a qualified template into a fresh independent workspace folder."""
    parts = selector.split(":")
    if (
        len(parts) != 2
        or parts[0] not in {"bundled", "local"}
        or not _NAME.fullmatch(parts[1])
    ):
        raise CatalogError(
            "invalid_selector", f"Use bundled:NAME or local:NAME: {selector}"
        )
    for entry in list_templates(catalog_home=catalog_home):
        if entry["selector"] == selector:
            result: dict[str, object] = _copy(
                Path(entry["path"]),
                destination,
                catalog_home=catalog_home,
                into_catalog=False,
            )
            result["provenance"] = {
                "template_id": selector,
                "plus_version": _plus_version(),
                "sha256": workflow_fingerprint(destination.expanduser().absolute()),
            }
            return result
    raise CatalogError("unknown_selector", f"Unknown template selector: {selector}")
