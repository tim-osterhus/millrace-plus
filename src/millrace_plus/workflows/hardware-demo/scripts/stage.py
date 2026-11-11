"""Captured command entrypoint; all domain behavior belongs to the Plus package."""

from argparse import ArgumentParser
from pathlib import Path

from millrace_plus.demo.stages import run_stage

parser = ArgumentParser()
parser.add_argument("stage")
parser.add_argument("--session-file", type=Path)
parser.add_argument("--no-web", action="store_true")
args = parser.parse_args()
raise SystemExit(
    run_stage(
        args.stage,
        workspace=Path.cwd(),
        session_file=args.session_file,
        no_web=args.no_web,
    )
)
