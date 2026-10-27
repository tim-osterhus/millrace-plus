"""Offline bootstrap proof only: no LAD roles, CLOSE/ACK or queue disposition."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def main() -> None:
    if sys.argv[1:] == ["--learning"]:
        print(
            json.dumps({"outcome": "DONE", "summary": "fixture workspace", "refs": []})
        )
        return
    assert sys.argv[1:] == []
    import lad_state

    foreground, project = lad_state.roots()
    selected = lad_state.shared_target(foreground)
    assert selected is not None
    assert (project / "lad-target.json").read_bytes() == lad_state.canonical(
        selected
    ) + b"\n"
    target = lad_state.target_root(foreground)
    assert str(target) == selected["target_root"]
    assert Path(lad_state.__file__).resolve().parent == Path(__file__).resolve().parent
    state = lad_state.load(foreground)
    _, identity, inputs = lad_state.manifest(project)
    assert state["identity_sha256"] == identity and state["inputs_sha256"] == inputs
    assert state["terminal"] is None
    assert (
        lad_state.source(foreground, ["input.txt"])["input.txt"]
        == hashlib.sha256(b"41\n").hexdigest()
    )
    outside = lad_state.outside_source(foreground)
    assert outside == {}  # No active packet in this deliberately pristine subset.
    answer = str(int((target / "input.txt").read_text()) + 1).encode() + b"\n"
    (target / "answer.txt").write_bytes(answer)
    answer_sha = lad_state.source(foreground, ["answer.txt"])["answer.txt"]
    assert answer == b"42\n" and answer_sha == hashlib.sha256(answer).hexdigest()
    proof = {
        "target_root": str(target),
        "claim_sha256": selected["claim_sha256"],
        "session_id": state["session_id"],
        "helper_capture": str(Path(lad_state.__file__).resolve()),
        "answer_sha256": answer_sha,
        "pristine_outside_source": outside,
    }
    (foreground / "bootstrap-proof.json").write_bytes(
        lad_state.canonical(proof) + b"\n"
    )
    print(
        json.dumps(
            {
                "outcome": "DONE",
                "summary": "pinned target 41 to 42",
                "refs": ["bootstrap-proof.json"],
            }
        )
    )


if __name__ == "__main__":
    main()
