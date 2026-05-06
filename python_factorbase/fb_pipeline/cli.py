from __future__ import annotations

import argparse
from pathlib import Path

from .bn_client import run_bn_learner
from .config import FBConfig
from .fmt_pipeline import FMTPipeline
from .setup_pipeline import SetupPipeline


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Python FactorBase orchestration CLI")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.cfg"),
        help="Path to FactorBase config.properties file",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("setup", help="Run setup/metadata SQL pipeline")
    subparsers.add_parser("fmt", help="Run FMT bootstrap stored procedures")
    subparsers.add_parser("setup-and-fmt", help="Run setup then FMT")

    bn_parser = subparsers.add_parser("bn-learn", help="Run standalone BN learner jar")
    bn_parser.add_argument("--jar", type=Path, required=True, help="Path to bnrunner jar")
    bn_parser.add_argument("--input-tsv", type=Path, required=True, help="Input CT TSV file")
    bn_parser.add_argument("--output-edges", type=Path, required=True, help="Output learned edges file")
    bn_parser.add_argument("--counts-column", default="MULT")
    bn_parser.add_argument("--discrete", choices=["true", "false"], default="true")
    bn_parser.add_argument("--required-edges", type=Path)
    bn_parser.add_argument("--forbidden-edges", type=Path)

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    config = FBConfig.from_file(args.config)
    scripts_dir = Path(__file__).resolve().parents[2] / "code" / "factorbase" / "src" / "main" / "resources" / "scripts"

    if args.command == "setup":
        SetupPipeline(config, scripts_dir).run()
        return

    if args.command == "fmt":
        FMTPipeline(config).run()
        return

    if args.command == "setup-and-fmt":
        SetupPipeline(config, scripts_dir).run()
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

    parser.error(f"Unsupported command: {args.command}")
