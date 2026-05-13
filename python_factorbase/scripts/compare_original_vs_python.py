#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import subprocess
from pathlib import Path
from urllib.parse import urlparse


def parse_properties(path: Path) -> dict[str, str]:
    properties: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        properties[key.strip()] = value.strip()
    return properties


def load_config(path: Path) -> dict[str, str]:
    values = parse_properties(path)
    dbaddress = values["dbaddress"]
    parsed = urlparse(dbaddress)
    return {
        "host": parsed.hostname or "127.0.0.1",
        "port": str(parsed.port or 3306),
        "user": values["dbusername"],
        "password": values.get("dbpassword", ""),
        "dbname": values["dbname"],
    }


def connect(cfg: dict[str, str], database: str | None = None):
    import mysql.connector

    return mysql.connector.connect(
        host=cfg["host"],
        port=int(cfg["port"]),
        user=cfg["user"],
        password=cfg["password"],
        database=database,
        autocommit=True,
    )


def run_command(command: list[str], cwd: Path) -> None:
    print("+", " ".join(command))
    subprocess.run(command, cwd=cwd, check=True)


def quote_identifier(identifier: str) -> str:
    return "`" + identifier.replace("`", "``") + "`"


def get_ct_table_count(connection, ct_schema: str) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_schema = %s
            """,
            (ct_schema,),
        )
        row = cursor.fetchone()
    return int(row[0]) if row is not None else 0


def get_largest_short_rchain(connection, bn_schema: str) -> str:
    query = (
        "SELECT lm.short_rnid "
        f"FROM {quote_identifier(bn_schema)}.lattice_set ls "
        f"JOIN {quote_identifier(bn_schema)}.lattice_mapping lm "
        "  ON lm.orig_rnid = ls.name "
        "WHERE ls.length = ("
        f"  SELECT MAX(length) FROM {quote_identifier(bn_schema)}.lattice_set"
        ") "
        "ORDER BY lm.short_rnid "
        "LIMIT 1"
    )
    with connection.cursor() as cursor:
        cursor.execute(query)
        row = cursor.fetchone()
    if row is None:
        raise RuntimeError(f"No lattice mapping rows found in schema '{bn_schema}'.")
    return str(row[0])


def export_table_to_tsv(
    connection,
    schema_name: str,
    table_name: str,
    output_tsv: Path,
    where_clause: str | None = None,
) -> None:
    output_tsv.parent.mkdir(parents=True, exist_ok=True)
    query = f"SELECT * FROM {quote_identifier(schema_name)}.{quote_identifier(table_name)}"
    if where_clause:
        query += f" WHERE {where_clause}"

    with connection.cursor() as cursor:
        cursor.execute(query)
        headers = [column[0] for column in cursor.description]
        with output_tsv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, delimiter="\t")
            writer.writerow(headers)
            for row in cursor:
                writer.writerow(["" if value is None else value for value in row])


def export_baseline_final_edges_to_tsv(connection, bn_schema: str, output_tsv: Path) -> None:
    output_tsv.parent.mkdir(parents=True, exist_ok=True)
    query = (
        "SELECT DISTINCT parent, child "
        f"FROM {quote_identifier(bn_schema)}.Final_Path_BayesNets "
        "ORDER BY parent, child"
    )
    with connection.cursor() as cursor:
        cursor.execute(query)
        with output_tsv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, delimiter="\t")
            writer.writerow(["parent", "child"])
            for row in cursor:
                parent = "" if row[0] is None else str(row[0])
                child = "" if row[1] is None else str(row[1])
                writer.writerow([parent, child])


def read_edge_set(path: Path) -> set[tuple[str, str]]:
    edges: set[tuple[str, str]] = set()
    with path.open("r", encoding="utf-8") as handle:
        reader = csv.reader(handle, delimiter="\t")
        header_skipped = False
        for row in reader:
            if not row:
                continue
            if not header_skipped:
                header_skipped = True
                if row[0].strip().lower() == "parent":
                    continue
            if len(row) < 2:
                continue
            edges.add((row[0].strip(), row[1].strip()))
    return edges


def write_summary(summary_path: Path, lines: list[str]) -> None:
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run original Java FactorBase and Python FactorBase flow, then compare "
            "CT counts and learned edges."
        )
    )
    parser.add_argument("--baseline-config", type=Path, required=True)
    parser.add_argument("--python-config", type=Path, required=True)
    parser.add_argument(
        "--factorbase-jar",
        type=Path,
        default=Path("code/factorbase/target/factorbase-1.0-SNAPSHOT.jar"),
    )
    parser.add_argument(
        "--bn-jar",
        type=Path,
        default=Path("code/bnrunner/target/bnrunner-1.0-SNAPSHOT.jar"),
    )
    parser.add_argument("--workdir", type=Path, default=Path("."))
    parser.add_argument("--outdir", type=Path, default=Path("compare_runs/latest"))
    parser.add_argument("--java-bin", default="java")
    parser.add_argument("--python-bin", default="python")
    args = parser.parse_args()

    workdir = args.workdir.resolve()
    outdir = (workdir / args.outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    baseline_config = load_config((workdir / args.baseline_config).resolve())
    python_config = load_config((workdir / args.python_config).resolve())

    baseline_bn_schema = f"{baseline_config['dbname']}_BN"
    baseline_ct_schema = f"{baseline_config['dbname']}_CT"
    python_bn_schema = f"{python_config['dbname']}_BN"
    python_ct_schema = f"{python_config['dbname']}_CT"

    factorbase_jar = (workdir / args.factorbase_jar).resolve()
    bn_jar = (workdir / args.bn_jar).resolve()
    baseline_cfg_path = (workdir / args.baseline_config).resolve()
    python_cfg_path = (workdir / args.python_config).resolve()

    if not factorbase_jar.exists():
        raise FileNotFoundError(f"Baseline FactorBase jar not found: {factorbase_jar}")
    if not bn_jar.exists():
        raise FileNotFoundError(f"BN learner jar not found: {bn_jar}")

    print("== 1) Run original Java FactorBase ==")
    run_command(
        [
            args.java_bin,
            f"-Dconfig={baseline_cfg_path}",
            "-jar",
            str(factorbase_jar),
        ],
        cwd=workdir,
    )

    print("== 2) Run Python setup + FMT ==")
    run_command(
        [
            args.python_bin,
            "python_factorbase/run.py",
            "--config",
            str(python_cfg_path),
            "setup-and-fmt",
        ],
        cwd=workdir,
    )

    print("== 3) Export CT tables and baseline final edges ==")
    baseline_conn = connect(baseline_config)
    python_conn = connect(python_config)
    try:
        baseline_ct_count = get_ct_table_count(baseline_conn, baseline_ct_schema)
        python_ct_count = get_ct_table_count(python_conn, python_ct_schema)

        baseline_short = get_largest_short_rchain(baseline_conn, baseline_bn_schema)
        python_short = get_largest_short_rchain(python_conn, python_bn_schema)

        baseline_ct_table = f"{baseline_short}_CT"
        python_ct_table = f"{python_short}_CT"

        baseline_ct_tsv = outdir / "baseline_largest_ct.tsv"
        python_ct_tsv = outdir / "python_largest_ct.tsv"
        baseline_final_edges_tsv = outdir / "baseline_final_edges.tsv"

        export_table_to_tsv(
            baseline_conn,
            baseline_ct_schema,
            baseline_ct_table,
            baseline_ct_tsv,
            where_clause="MULT > 0",
        )
        export_table_to_tsv(
            python_conn,
            python_ct_schema,
            python_ct_table,
            python_ct_tsv,
            where_clause="MULT > 0",
        )
        export_baseline_final_edges_to_tsv(
            baseline_conn,
            baseline_bn_schema,
            baseline_final_edges_tsv,
        )
    finally:
        baseline_conn.close()
        python_conn.close()

    print("== 4) Run BN learner jar on baseline CT TSV ==")
    baseline_bnlearn_edges = outdir / "baseline_bnlearn_edges.tsv"
    python_bnlearn_edges = outdir / "python_bnlearn_edges.tsv"

    run_command(
        [
            args.python_bin,
            "python_factorbase/run.py",
            "--config",
            str(python_cfg_path),
            "bn-learn",
            "--jar",
            str(bn_jar),
            "--input-tsv",
            str(baseline_ct_tsv),
            "--output-edges",
            str(baseline_bnlearn_edges),
        ],
        cwd=workdir,
    )

    print("== 5) Run Python one-shot pipeline (setup + FMT + bn-learn) ==")
    run_command(
        [
            args.python_bin,
            "python_factorbase/run.py",
            "--config",
            str(python_cfg_path),
            "setup-and-fmt-and-bn-learn",
            "--jar",
            str(bn_jar),
            "--input-tsv",
            str(python_ct_tsv),
            "--output-edges",
            str(python_bnlearn_edges),
        ],
        cwd=workdir,
    )

    print("== 6) Compare results ==")
    baseline_final_edges = read_edge_set(outdir / "baseline_final_edges.tsv")
    baseline_bnlearn_set = read_edge_set(outdir / "baseline_bnlearn_edges.tsv")
    python_bnlearn_set = read_edge_set(outdir / "python_bnlearn_edges.tsv")

    ct_count_same = baseline_ct_count == python_ct_count
    bnlearn_same = baseline_bnlearn_set == python_bnlearn_set
    final_vs_python_same = baseline_final_edges == python_bnlearn_set

    summary_lines = [
        f"Baseline dbname: {baseline_config['dbname']}",
        f"Python dbname: {python_config['dbname']}",
        f"Baseline largest short_rnid: {baseline_short}",
        f"Python largest short_rnid: {python_short}",
        f"Baseline largest CT table: {baseline_ct_schema}.{baseline_ct_table}",
        f"Python largest CT table: {python_ct_schema}.{python_ct_table}",
        f"CT table count baseline: {baseline_ct_count}",
        f"CT table count python: {python_ct_count}",
        f"CT table count equal: {ct_count_same}",
        "",
        f"Baseline bn-learn edge count: {len(baseline_bnlearn_set)}",
        f"Python one-shot (setup+fmt+bn-learn) edge count: {len(python_bnlearn_set)}",
        f"bn-learn edges equal (baseline CT vs python one-shot): {bnlearn_same}",
        "",
        f"Baseline Final_Path_BayesNets edge count: {len(baseline_final_edges)}",
        f"Baseline Final_Path_BayesNets vs python bn-learn equal: {final_vs_python_same}",
        "",
        f"Output directory: {outdir}",
    ]
    write_summary(outdir / "summary.txt", summary_lines)
    print("\n".join(summary_lines))


if __name__ == "__main__":
    main()
