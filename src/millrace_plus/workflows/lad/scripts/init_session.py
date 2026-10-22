"""Fresh-only atomic LAD bootstrap; every launcher reaches this graph node."""

from __future__ import annotations

import argparse
import os

from lad_state import (
    LadError,
    atomic,
    canonical,
    emit,
    load,
    manifest,
    new_state,
    roots,
    run,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True)
    args = parser.parse_args()
    foreground, project = roots(args.project_root)
    _, identity, inputs = manifest(project)
    directory = foreground / "lad-state"
    if directory.exists() or directory.is_symlink():
        state = load(foreground)
        if (state["identity_sha256"], state["inputs_sha256"]) != (identity, inputs):
            raise LadError("session_input_identity_changed")
        used = bool(
            state["events"]
            or state["occurrences"]
            or state["accepted"]
            or state["pending"]
            or state["request"]
            or state["terminal"]
        )
        emit(
            "EXISTING_SESSION_WITH_WORK" if used else "EXISTING_SESSION_PRISTINE",
            "session="
            + state["session_id"]
            + "; continuation="
            + str(state["continuation"])
            + "; terminal="
            + str(state["terminal"]),
        )
        return
    for workspace in (foreground, project / "learning"):
        if any(
            (workspace / name).exists() or (workspace / name).is_symlink()
            for name in (
                "lad-state",
                "lad-results",
                "candidates",
                "lad-work-marker.json",
                "work",
                "occurrences",
            )
        ):
            raise LadError("missing_session_after_work")
    staging = foreground / ".lad-init-session"
    if staging.exists() or staging.is_symlink():
        if staging.is_symlink() or not staging.is_dir():
            raise LadError("initializer_staging_conflict")
        from lad_state import load_json, read

        owner = load_json(read(foreground, ".lad-init-session/owner.json"))
        entries = list(staging.iterdir())
        if any(
            not p.is_file()
            or p.is_symlink()
            or (
                p.name not in {"owner.json", "session.json"}
                and not p.name.startswith(".lad-write-")
            )
            for p in entries
        ):
            raise LadError("initializer_staging_conflict")
        staged = (
            load_json(read(foreground, ".lad-init-session/session.json"))
            if (staging / "session.json").exists()
            else None
        )
        if owner != {
            "format": "millrace-lad-initializer/v1",
            "identity": identity,
            "inputs": inputs,
        } or (
            staged is not None
            and (
                staged["events"]
                or staged["occurrences"]
                or staged["accepted"]
                or staged["identity_sha256"] != identity
                or staged["inputs_sha256"] != inputs
            )
        ):
            raise LadError("initializer_staging_conflict")
        for entry in entries:
            entry.unlink()
        staging.rmdir()
    staging.mkdir(mode=0o700)
    state = new_state(identity, inputs)
    atomic(
        foreground,
        ".lad-init-session/owner.json",
        canonical(
            {
                "format": "millrace-lad-initializer/v1",
                "identity": identity,
                "inputs": inputs,
            }
        ),
    )
    atomic(foreground, ".lad-init-session/session.json", canonical(state) + b"\n")
    # Rename publishes the complete directory. No role can run before this exits.
    os.rename(staging, directory)
    atomic(
        foreground,
        "lad-work-marker.json",
        canonical({"session_id": state["session_id"]}),
        immutable=True,
    )
    emit("NEW_SESSION", "session=" + state["session_id"])


if __name__ == "__main__":
    # Bootstrap corruption has its own declared outcome, never fresh fallback.
    def checked() -> None:
        try:
            main()
        except (LadError, OSError, ValueError, KeyError, TypeError) as exc:
            emit(
                "RECONCILE",
                str(exc) if isinstance(exc, LadError) else type(exc).__name__,
            )

    run(checked)
