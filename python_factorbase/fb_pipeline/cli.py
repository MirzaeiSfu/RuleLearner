from __future__ import annotations

import argparse
from pathlib import Path

from .bn_client import run_bn_learner
from .config import FBConfig
from .db import SourceSchemaError
from .fmt_pipeline import FMTPipeline
from .setup_pipeline import SetupPipeline


def _add_setup_safety_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--keep-run-metadata",
        action="store_true",
        help=(
            "Do not drop <dbname>.run_metadata before setup. "
            "Default behavior is to drop it for GraphVAE-safe repeated runs."
        ),
    )


def _add_bn_learner_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--jar",
        type=Path,
        default=Path("code/bnrunner/target/bnrunner-1.0-SNAPSHOT.jar"),
        help="Path to BN learner jar (default: code/bnrunner/target/bnrunner-1.0-SNAPSHOT.jar)",
    )
    parser.add_argument("--input-tsv", type=Path, required=True, help="Input CT TSV file")
    parser.add_argument("--output-edges", type=Path, required=True, help="Output learned edges file")
    parser.add_argument("--counts-column", default="MULT")
    parser.add_argument("--discrete", choices=["true", "false"], default="true")
    parser.add_argument("--required-edges", type=Path)
    parser.add_argument("--forbidden-edges", type=Path)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Python FactorBase orchestration CLI")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.cfg"),
        help="Path to FactorBase config.properties file",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    setup_parser = subparsers.add_parser("setup", help="Run setup/metadata SQL pipeline")
    _add_setup_safety_flags(setup_parser)
    subparsers.add_parser("fmt", help="Run FMT bootstrap stored procedures")
    setup_fmt_parser = subparsers.add_parser("setup-and-fmt", help="Run setup then FMT")
    _add_setup_safety_flags(setup_fmt_parser)

    bn_parser = subparsers.add_parser("bn-learn", help="Run standalone BN learner jar")
    _add_bn_learner_args(bn_parser)

    setup_fmt_bn_parser = subparsers.add_parser(
        "setup-and-fmt-and-bn-learn",
        help="Run setup, FMT, then standalone BN learner jar",
    )
    _add_setup_safety_flags(setup_fmt_bn_parser)
    _add_bn_learner_args(setup_fmt_bn_parser)

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    try:
        config = FBConfig.from_file(args.config)
        scripts_dir = Path(__file__).resolve().parents[2] / "code" / "factorbase" / "src" / "main" / "resources" / "scripts"

        if args.command == "setup":
            SetupPipeline(
                config,
                scripts_dir,
                drop_run_metadata=not args.keep_run_metadata,
            ).run()
            return

        if args.command == "fmt":
            FMTPipeline(config).run()
            return

        if args.command == "setup-and-fmt":
            SetupPipeline(
                config,
                scripts_dir,
                drop_run_metadata=not args.keep_run_metadata,
            ).run()
            FMTPipeline(config).run()
            return

        if args.command == "bn-learn":
            run_bn_learner(
                jar_path=args.jar,
                input_tsv=args.input_tsv,
                output_edges=args.output_edges,
                counts_column=args.counts_column,
                discrete=args.discrete == "true",
                required_edges=args.required_edges,
                forbidden_edges=args.forbidden_edges,
            )
            return

        if args.command == "setup-and-fmt-and-bn-learn":
            SetupPipeline(
                config,
                scripts_dir,
                drop_run_metadata=not args.keep_run_metadata,
            ).run()
            FMTPipeline(config).run()
            run_bn_learner(
                jar_path=args.jar,
                input_tsv=args.input_tsv,
                output_edges=args.output_edges,
                counts_column=args.counts_column,
                discrete=args.discrete == "true",
                required_edges=args.required_edges,
                forbidden_edges=args.forbidden_edges,
            )
            return

        parser.error(f"Unsupported command: {args.command}")
    except SourceSchemaError as exc:
        parser.exit(1, f"Error: {exc}\n")
