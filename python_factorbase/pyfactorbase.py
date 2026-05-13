from __future__ import annotations

import argparse
from pathlib import Path

from fb_pipeline.config import FBConfig
from fb_pipeline.factorbase_counterpart import run_factorbase_counterpart


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Python FactorBase counterpart runner. "
            "Config-driven flow similar to: java -Dconfig=... -jar factorbase.jar"
        )
    )
    parser.add_argument(
        "--config",
        "-Dconfig",
        type=Path,
        default=Path("config.cfg"),
        help="Path to FactorBase config file",
    )
    parser.add_argument(
        "--jarlearner",
        "-jarlearner",
        type=Path,
        default=Path("code/bnrunner/target/bnrunner-1.0-SNAPSHOT.jar"),
        help="Path to standalone BN learner jar",
    )
    parser.add_argument(
        "--keep-run-metadata",
        action="store_true",
        help=(
            "Do not drop <dbname>.run_metadata before setup. "
            "Default behavior is to drop it for GraphVAE-safe repeated runs."
        ),
    )
    args = parser.parse_args()

    config = FBConfig.from_file(args.config)
    scripts_dir = Path(__file__).resolve().parents[1] / "code" / "factorbase" / "src" / "main" / "resources" / "scripts"
    run_factorbase_counterpart(
        config=config,
        scripts_dir=scripts_dir,
        jar_path=args.jarlearner,
        drop_run_metadata=not args.keep_run_metadata,
    )


if __name__ == "__main__":
    main()
