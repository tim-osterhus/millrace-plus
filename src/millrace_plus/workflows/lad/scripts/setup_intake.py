"""Materialize the original request's exact UTF-8 bytes without a caller hook."""

from __future__ import annotations

import argparse

from lad_state import (
    LadError,
    atomic,
    digest,
    emit,
    load,
    manifest,
    read,
    roots,
    run,
    save,
    text,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--greenfield", action="store_true")
    args = parser.parse_args()
    foreground, project = roots(args.project_root)
    try:
        state = load(foreground)
        if state["events"] or state["pending"] or state["request"]:
            raise LadError("intake_after_work")
        obj, identity, inputs = manifest(project)
        if (identity, inputs) != (state["identity_sha256"], state["inputs_sha256"]):
            raise LadError("bootstrap_manifest_changed")
        if obj is not None:
            raw = text(obj["inputs"].get("request_markdown")).encode("utf-8")
            origin = "subworkflow-inputs.json:inputs.request_markdown"
        elif (
            not (project / "request.md").exists()
            and not (project / "request.md").is_symlink()
        ):
            emit(
                "INTAKE_MISSING",
                "Create a nonblank server-local project request.md; "
                "resume setup_intake.",
            )
            return
        else:
            raw = read(project, "request.md")
            text(raw.decode("utf-8"))
            origin = "request.md"
        for root in (project, foreground):
            file = root / "request.md"
            if (file.exists() or file.is_symlink()) and read(root, "request.md") != raw:
                raise LadError("request_bytes_conflict")
        expected = {
            "source": origin,
            "sha256": digest(raw),
            "greenfield": args.greenfield,
        }
        if state["intake"] is not None and state["intake"] != expected:
            raise LadError("intake_identity_changed")
        atomic(foreground, "request.md", raw, immutable=True)
        state["intake"] = expected
        save(foreground, state)
        emit(
            "INTAKE_READY_GREENFIELD" if args.greenfield else "INTAKE_READY_EXISTING",
            "Original request preserved byte-for-byte.",
            ["request.md"],
        )
    except (LadError, OSError, ValueError, TypeError, UnicodeError) as exc:
        emit(
            "INTAKE_CONFLICT",
            str(exc) if isinstance(exc, LadError) else type(exc).__name__,
        )


if __name__ == "__main__":
    run(main)
