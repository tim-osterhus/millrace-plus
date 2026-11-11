"""Offline exact effect peer. Its only effect is an owned JSON ledger.

This executable invokes no Git, network, provider or shell. Lost responses and
absence proofs exercise the product's saved-intent protocol, not real commits.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("request", type=Path)
    parser.add_argument("result", type=Path)
    args = parser.parse_args()
    request = json.loads(args.request.read_bytes())
    operation = request["operation"]
    intent = request | {"operation": "execute"}
    if operation == "execute":
        state = json.loads(
            (args.request.parent / (request["input_id"] + ".state.json")).read_bytes()
        )
        reserved = json.loads(
            (args.request.parent / state["delivery_refs"][-1]["path"]).read_bytes()
        )
        saved = json.loads(
            (args.request.parent / reserved["intent_ref"]["path"]).read_bytes()
        )
        assert reserved["status"] == "reserved" and reserved["operation"] == "execute"
        assert reserved["process_ref"] is None and saved == intent
    args.ledger.mkdir(exist_ok=True)
    log = args.ledger / "operations.jsonl"
    previous = (
        []
        if not log.exists()
        else [json.loads(line) for line in log.read_text().splitlines()]
    )
    with log.open("ab") as stream:
        stream.write(
            canonical(
                {
                    "operation": operation,
                    "effect_id": request["effect_id"],
                    "intent_sha256": digest(intent),
                    "target_sha256": digest(request["target"]),
                    "saved_before_execute": operation == "execute",
                }
            )
            + b"\n"
        )
        stream.flush()
    applied_path = args.ledger / (request["effect_id"] + ".applied.json")
    applied = json.loads(applied_path.read_bytes()) if applied_path.exists() else None
    status = "prepared"
    if operation == "execute":
        if applied is not None:
            raise SystemExit("duplicate execute after applied effect")
        count = 1 + sum(row["operation"] == "execute" for row in previous)
        if args.scenario == "pending":
            status = "pending"
        elif args.scenario == "not_applied_once" and count == 1:
            status = "not_applied"
        else:
            applied = {
                "effect_id": request["effect_id"],
                "intent_sha256": digest(intent),
                "target_sha256": digest(request["target"]),
                "remote_id": "fake-commit:" + request["effect_id"],
                "observed_head": hashlib.sha1(canonical(intent)).hexdigest(),
            }
            applied_path.write_bytes(canonical(applied))
            status = "applied"
            if args.scenario == "lost_applied":
                return
            if args.scenario == "malformed_applied":
                args.result.write_bytes(b'{"incomplete":')
                return
    elif operation == "inspect":
        if applied is not None:
            if applied["intent_sha256"] != digest(intent) or applied[
                "target_sha256"
            ] != digest(request["target"]):
                raise SystemExit("conflicting inspection identity")
            status = "applied"
        else:
            status = "pending" if args.scenario == "pending" else "not_applied"
    remote = applied["remote_id"] if status == "applied" else None
    head = applied["observed_head"] if status == "applied" else None
    refs = []
    if status in {"applied", "not_applied"}:
        proof = {
            "format": "millrace-plus-cicd-effect-evidence/v1",
            "effect_id": request["effect_id"],
            "intent_sha256": digest(intent),
            "target_sha256": digest(request["target"]),
            "status": status,
            "remote_id": remote,
            "observed_head": head,
        }
        proof_path = args.result.with_suffix(".proof.json")
        raw = canonical(proof)
        proof_path.write_bytes(raw)
        refs = [{"path": proof_path.name, "sha256": hashlib.sha256(raw).hexdigest()}]
    args.result.write_bytes(
        canonical(
            {
                "format": "millrace-plus-cicd-effect-result/v2",
                "delivery_id": request["delivery_id"],
                "effect_id": request["effect_id"],
                "request_sha256": digest(request),
                "operation": operation,
                "status": status,
                "remote_id": remote,
                "observed_head": head,
                "artifact_sha256": None,
                "evidence_refs": refs,
                "observed_at": datetime.now(UTC).isoformat(),
                "diagnostic": None,
            }
        )
    )


if __name__ == "__main__":
    main()
