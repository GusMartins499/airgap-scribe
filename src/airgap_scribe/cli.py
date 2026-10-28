import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from airgap_scribe import cleanup, pipeline, prepare, verify
from airgap_scribe.config import ConfigError, load_config, load_container_config
from airgap_scribe.naming import parse_meeting_file

EXIT_USAGE = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="airgap-scribe",
        description="Turn meeting recordings into anonymized written drafts.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    prepare_parser = commands.add_parser(
        "prepare", help="convert, transcribe and anonymize one recording (container)"
    )
    prepare_parser.add_argument("--input", type=Path, required=True)
    prepare_parser.add_argument("--workdir", type=Path, required=True)
    prepare_parser.add_argument("--config", type=Path, required=True)
    prepare_parser.add_argument("--transcript", type=Path)
    prepare_parser.set_defaults(handler=_prepare)

    verify_parser = commands.add_parser(
        "verify", help="check a draft for names that survived anonymization (container)"
    )
    verify_parser.add_argument("--workdir", type=Path, required=True)
    verify_parser.add_argument("--config", type=Path, required=True)
    verify_parser.set_defaults(handler=_verify)

    run_parser = commands.add_parser("run", help="process one recording end to end (host)")
    run_parser.add_argument("file", type=Path)
    run_parser.add_argument("--config", type=Path, required=True)
    run_parser.set_defaults(handler=_run)

    scan_parser = commands.add_parser("scan", help="process every pending recording (host)")
    scan_parser.add_argument("--config", type=Path, required=True)
    scan_parser.set_defaults(handler=_scan)

    cleanup_parser = commands.add_parser("cleanup", help="remove work data of merged drafts (host)")
    cleanup_parser.add_argument("--config", type=Path, required=True)
    cleanup_parser.set_defaults(handler=_cleanup)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except ConfigError as error:
        print(f"airgap-scribe: configuration error: {error}", file=sys.stderr)
        return EXIT_USAGE


def _prepare(args: argparse.Namespace) -> int:
    config = load_container_config(args.config)
    prepare.prepare(args.input, args.transcript, args.workdir, config)
    return 0


def _verify(args: argparse.Namespace) -> int:
    config = load_container_config(args.config)
    alerts = verify.run_verify(args.workdir, config)
    print(f"{len(alerts)} alert(s)")
    return 0


def _run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    meeting = parse_meeting_file(args.file, config.kinds)
    if meeting is None:
        print(
            f"airgap-scribe: not a recognized meeting file: {args.file.name}",
            file=sys.stderr,
        )
        return EXIT_USAGE
    pipeline.run_one(meeting, config)
    return 0


def _scan(args: argparse.Namespace) -> int:
    pipeline.scan(load_config(args.config))
    return 0


def _cleanup(args: argparse.Namespace) -> int:
    for stem in cleanup.cleanup(load_config(args.config)):
        print(stem)
    return 0
