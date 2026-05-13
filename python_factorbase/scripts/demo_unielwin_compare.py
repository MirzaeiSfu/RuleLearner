#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse


ROOT_DIR = Path(__file__).resolve().parents[2]
BASELINE_CONFIG = ROOT_DIR / "python_factorbase" / "configs" / "unielwin_baseline.cfg"
PYTHON_CONFIG = ROOT_DIR / "python_factorbase" / "configs" / "unielwin_python.cfg"
SUMMARY_PATH = ROOT_DIR / "compare_runs" / "unielwin_3line" / "summary.txt"


def parse_properties(path: Path) -> dict[str, str]:
    props: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        props[key.strip()] = value.strip()
    return props


def connect_from_cfg(cfg: dict[str, str]):
    import mysql.connector

    parsed = urlparse(cfg["dbaddress"])
    return mysql.connector.connect(
        host=parsed.hostname or "127.0.0.1",
        port=parsed.port or 3306,
        user=cfg["dbusername"],
        password=cfg.get("dbpassword", ""),
        autocommit=True,
    )


def quote_identifier(identifier: str) -> str:
    return "`" + identifier.replace("`", "``") + "`"


def fetch_final_path_edges(connection, bn_schema: str) -> set[tuple[str, str]]:
    query = (
        "SELECT DISTINCT parent, child "
        f"FROM {quote_identifier(bn_schema)}.Final_Path_BayesNets "
        "ORDER BY parent, child"
    )
    edges: set[tuple[str, str]] = set()
    with connection.cursor() as cursor:
        cursor.execute(query)
        for parent, child in cursor:
            edges.add(("" if parent is None else str(parent), "" if child is None else str(child)))
    return edges


def fetch_ct_count(connection, ct_schema: str) -> int:
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


def main() -> None:
    if not BASELINE_CONFIG.exists():
        raise FileNotFoundError(f"Missing config: {BASELINE_CONFIG}")
    if not PYTHON_CONFIG.exists():
        raise FileNotFoundError(f"Missing config: {PYTHON_CONFIG}")

    baseline_cfg = parse_properties(BASELINE_CONFIG)
    python_cfg = parse_properties(PYTHON_CONFIG)

    baseline_bn_schema = f"{baseline_cfg['dbname']}_BN"
    baseline_ct_schema = f"{baseline_cfg['dbname']}_CT"
    python_bn_schema = f"{python_cfg['dbname']}_BN"
    python_ct_schema = f"{python_cfg['dbname']}_CT"

    with connect_from_cfg(baseline_cfg) as baseline_conn:
        baseline_edges = fetch_final_path_edges(baseline_conn, baseline_bn_schema)
        baseline_ct_count = fetch_ct_count(baseline_conn, baseline_ct_schema)

    with connect_from_cfg(python_cfg) as python_conn:
        python_edges = fetch_final_path_edges(python_conn, python_bn_schema)
        python_ct_count = fetch_ct_count(python_conn, python_ct_schema)

    only_baseline = sorted(baseline_edges - python_edges)
    only_python = sorted(python_edges - baseline_edges)
    same_edges = not only_baseline and not only_python
    same_ct_count = baseline_ct_count == python_ct_count

    lines = [
        f"Baseline dbname: {baseline_cfg['dbname']}",
        f"Python dbname: {python_cfg['dbname']}",
        f"CT table count baseline: {baseline_ct_count}",
        f"CT table count python: {python_ct_count}",
        f"CT table count equal: {same_ct_count}",
        f"Baseline edge count: {len(baseline_edges)}",
        f"Python edge count: {len(python_edges)}",
        f"Edge sets equal: {same_edges}",
    ]

    if only_baseline:
        lines.append("Only in baseline (first 10):")
        for edge in only_baseline[:10]:
            lines.append(f"  {edge[0]} -> {edge[1]}")
    if only_python:
        lines.append("Only in python (first 10):")
        for edge in only_python[:10]:
            lines.append(f"  {edge[0]} -> {edge[1]}")

    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"Summary written to: {SUMMARY_PATH}")


if __name__ == "__main__":
    main()
