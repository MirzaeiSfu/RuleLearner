from __future__ import annotations

import argparse
import csv
from pathlib import Path

from .bn_client import run_bn_learner
from .config import FBConfig
from .db import SourceSchemaError, connect
from .factorbase_counterpart import run_factorbase_counterpart
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


def _add_bn_jar_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--jar",
        "-jarlearner",
        type=Path,
        default=Path("code/bnrunner/target/bnrunner-1.0-SNAPSHOT.jar"),
        help="Path to BN learner jar (default: code/bnrunner/target/bnrunner-1.0-SNAPSHOT.jar)",
    )


def _add_bn_common_args(parser: argparse.ArgumentParser) -> None:
    _add_bn_jar_arg(parser)
    parser.add_argument("--counts-column", default="MULT")
    parser.add_argument("--discrete", choices=["true", "false"], default="true")
    parser.add_argument("--required-edges", type=Path)
    parser.add_argument("--forbidden-edges", type=Path)


def _add_bn_learner_args(parser: argparse.ArgumentParser) -> None:
    _add_bn_common_args(parser)
    parser.add_argument("--input-tsv", type=Path, required=True, help="Input CT TSV file")
    parser.add_argument("--output-edges", type=Path, required=True, help="Output learned edges file")


def _quote_identifier(identifier: str) -> str:
    return "`" + identifier.replace("`", "``") + "`"


def _largest_ct_table_name(config: FBConfig) -> str:
    query = (
        "SELECT ls.name AS rchain, lm.short_rnid "
        f"FROM {_quote_identifier(config.bn_db)}.lattice_set ls "
        f"JOIN {_quote_identifier(config.bn_db)}.lattice_mapping lm "
        "  ON lm.orig_rnid = ls.name "
        "WHERE ls.length = ("
        f"  SELECT MAX(length) FROM {_quote_identifier(config.bn_db)}.lattice_set"
        ") "
        "ORDER BY lm.short_rnid "
        "LIMIT 1"
    )

    connection = connect(config)
    try:
        with connection.cursor() as cursor:
            cursor.execute(query)
            row = cursor.fetchone()
    finally:
        connection.close()

    if row is None:
        raise RuntimeError(f"No lattice mapping rows found in schema '{config.bn_db}'.")
    short_rnid = str(row[1])
    return f"{short_rnid}_CT"


def _largest_rchain_name(config: FBConfig) -> str:
    query = (
        "SELECT ls.name AS rchain "
        f"FROM {_quote_identifier(config.bn_db)}.lattice_set ls "
        "WHERE ls.length = ("
        f"  SELECT MAX(length) FROM {_quote_identifier(config.bn_db)}.lattice_set"
        ") "
        "ORDER BY ls.name "
        "LIMIT 1"
    )

    connection = connect(config)
    try:
        with connection.cursor() as cursor:
            cursor.execute(query)
            row = cursor.fetchone()
    finally:
        connection.close()

    if row is None:
        raise RuntimeError(f"No largest rchain found in schema '{config.bn_db}'.")
    return str(row[0])


def _export_ct_table_to_tsv(
    config: FBConfig,
    ct_table_name: str,
    output_tsv: Path,
    where_clause: str = "MULT > 0",
) -> None:
    output_tsv.parent.mkdir(parents=True, exist_ok=True)
    query = f"SELECT * FROM {_quote_identifier(config.ct_db)}.{_quote_identifier(ct_table_name)}"
    if where_clause:
        query += f" WHERE {where_clause}"

    connection = connect(config)
    try:
        with connection.cursor() as cursor:
            cursor.execute(query)
            headers = [column[0] for column in cursor.description]
            with output_tsv.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle, delimiter="\t")
                writer.writerow(headers)
                for row in cursor:
                    writer.writerow(["" if value is None else value for value in row])
    finally:
        connection.close()


def _add_auto_bn_args(parser: argparse.ArgumentParser) -> None:
    _add_bn_common_args(parser)
    parser.add_argument(
        "--output-edges",
        type=Path,
        default=Path("compare_runs/latest/python_edges.tsv"),
        help="Output learned edges file (default: compare_runs/latest/python_edges.tsv)",
    )
    parser.add_argument(
        "--ct-tsv",
        type=Path,
        default=Path("compare_runs/latest/python_largest_ct.tsv"),
        help="Path to write exported largest CT TSV (default: compare_runs/latest/python_largest_ct.tsv)",
    )


def _read_edge_rows(path: Path) -> list[tuple[str, str]]:
    edges: list[tuple[str, str]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t")
        for index, row in enumerate(reader):
            if not row or len(row) < 2:
                continue
            if index == 0 and row[0].strip().lower() == "parent":
                continue
            parent = row[0].strip()
            child = row[1].strip()
            if not child:
                continue
            edges.append((parent, child))
    return edges


def _publish_edges_to_bn_tables(
    config: FBConfig,
    rchain: str,
    edges_tsv: Path,
) -> None:
    edges = _read_edge_rows(edges_tsv)
    connection = connect(config)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"DELETE FROM {_quote_identifier(config.bn_db)}.Path_BayesNets WHERE Rchain = %s",
                (rchain,),
            )

            insert_query = (
                f"INSERT IGNORE INTO {_quote_identifier(config.bn_db)}.Path_BayesNets "
                "(Rchain, child, parent) VALUES (%s, %s, %s)"
            )
            for parent, child in edges:
                cursor.execute(insert_query, (rchain, child, parent))

            cursor.execute(
                f"DROP TABLE IF EXISTS {_quote_identifier(config.bn_db)}.Final_Path_BayesNets"
            )
            cursor.execute(
                f"CREATE TABLE {_quote_identifier(config.bn_db)}.Final_Path_BayesNets AS "
                f"SELECT * FROM {_quote_identifier(config.bn_db)}.Path_BayesNets "
                "WHERE Rchain = %s AND parent <> ''",
                (rchain,),
            )
            cursor.execute(
                f"ALTER TABLE {_quote_identifier(config.bn_db)}.Final_Path_BayesNets "
                "ADD PRIMARY KEY (Rchain, child, parent)"
            )
        connection.commit()
    finally:
        connection.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Python FactorBase orchestration CLI")
    parser.add_argument(
        "--config",
        "-Dconfig",
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

    auto_bn_parser = subparsers.add_parser(
        "setup-and-fmt-and-auto-bn-learn",
        help="Run setup, FMT, export largest CT TSV, then run standalone BN learner jar",
    )
    _add_setup_safety_flags(auto_bn_parser)
    _add_auto_bn_args(auto_bn_parser)

    counterpart_parser = subparsers.add_parser(
        "factorbase-counterpart",
        help=(
            "Run Python counterpart flow for FactorBase structure learning "
            "(setup/FMT + multi-level BN learning + Final_Path_BayesNets)."
        ),
    )
    _add_setup_safety_flags(counterpart_parser)
    _add_bn_jar_arg(counterpart_parser)

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

        if args.command == "setup-and-fmt-and-auto-bn-learn":
            SetupPipeline(
                config,
                scripts_dir,
                drop_run_metadata=not args.keep_run_metadata,
            ).run()
            FMTPipeline(config).run()

            largest_ct_table = _largest_ct_table_name(config)
            largest_rchain = _largest_rchain_name(config)
            _export_ct_table_to_tsv(
                config=config,
                ct_table_name=largest_ct_table,
                output_tsv=args.ct_tsv,
            )

            run_bn_learner(
                jar_path=args.jar,
                input_tsv=args.ct_tsv,
                output_edges=args.output_edges,
                counts_column=args.counts_column,
                discrete=args.discrete == "true",
                required_edges=args.required_edges,
                forbidden_edges=args.forbidden_edges,
            )

            _publish_edges_to_bn_tables(
                config=config,
                rchain=largest_rchain,
                edges_tsv=args.output_edges,
            )
            return

        if args.command == "factorbase-counterpart":
            run_factorbase_counterpart(
                config=config,
                scripts_dir=scripts_dir,
                jar_path=args.jar,
                drop_run_metadata=not args.keep_run_metadata,
            )
            return

        parser.error(f"Unsupported command: {args.command}")
    except SourceSchemaError as exc:
        parser.exit(1, f"Error: {exc}\n")
