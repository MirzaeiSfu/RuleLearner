#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 3306
DEFAULT_USER = "fbuser"
DEFAULT_PASSWORD = ""
GENERATED_SCHEMA_SUFFIXES = ("_setup", "_BN", "_CT", "_CT_cache", "_global_counts")
DEFAULT_FUNCTIONAL_IGNORE_TABLES = ("CallLogs",)


def connect(host: str, port: int, user: str, password: str, database: str | None = None):
    import mysql.connector

    return mysql.connector.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        autocommit=True,
    )


def quote_identifier(identifier: str) -> str:
    return "`" + identifier.replace("`", "``") + "`"


def schema_exists(connection, schema_name: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.schemata
            WHERE schema_name = %s
            """,
            (schema_name,),
        )
        row = cursor.fetchone()
    return bool(row and int(row[0]) > 0)


def fetch_schema_objects(connection, schema_name: str) -> dict[str, str]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT table_name, table_type
            FROM information_schema.tables
            WHERE table_schema = %s
            ORDER BY table_name
            """,
            (schema_name,),
        )
        return {str(name): str(table_type) for name, table_type in cursor.fetchall()}


def fetch_columns(connection, schema_name: str, object_name: str) -> list[dict[str, str]]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT column_name, column_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position
            """,
            (schema_name, object_name),
        )
        return [
            {
                "name": str(column_name),
                "type": str(column_type),
                "nullable": str(is_nullable),
            }
            for column_name, column_type, is_nullable in cursor.fetchall()
        ]


def fetch_row_count(connection, schema_name: str, object_name: str) -> int:
    query = f"SELECT COUNT(*) FROM {quote_identifier(schema_name)}.{quote_identifier(object_name)}"
    with connection.cursor() as cursor:
        cursor.execute(query)
        row = cursor.fetchone()
    return int(row[0]) if row is not None else 0


def _serialize_value(value: object) -> str:
    if value is None:
        return "null:"
    if isinstance(value, bytes):
        return "bytes:" + value.hex()
    return f"{type(value).__name__}:{value}"


def fetch_content_hash(
    connection,
    schema_name: str,
    object_name: str,
    columns: list[dict[str, str]],
) -> str:
    digest = hashlib.sha256()
    query = f"SELECT * FROM {quote_identifier(schema_name)}.{quote_identifier(object_name)}"
    if columns:
        order_by = ", ".join(quote_identifier(column["name"]) for column in columns)
        query += f" ORDER BY {order_by}"

    with connection.cursor() as cursor:
        cursor.execute(query)
        for row in cursor:
            serialized_row = "\x1f".join(_serialize_value(value) for value in row)
            digest.update(serialized_row.encode("utf-8"))
            digest.update(b"\x1e")
    return digest.hexdigest()


def compare_schema(
    connection,
    baseline_schema: str,
    python_schema: str,
    ignored_tables: set[str],
) -> dict[str, object]:
    baseline_exists = schema_exists(connection, baseline_schema)
    python_exists = schema_exists(connection, python_schema)
    result: dict[str, object] = {
        "baseline_schema": baseline_schema,
        "python_schema": python_schema,
        "baseline_exists": baseline_exists,
        "python_exists": python_exists,
        "ignored_tables": sorted(ignored_tables),
    }

    if not baseline_exists or not python_exists:
        result["equal"] = baseline_exists == python_exists
        return result

    baseline_objects = fetch_schema_objects(connection, baseline_schema)
    python_objects = fetch_schema_objects(connection, python_schema)
    baseline_names_all = set(baseline_objects)
    python_names_all = set(python_objects)
    baseline_ignored = sorted(baseline_names_all & ignored_tables)
    python_ignored = sorted(python_names_all & ignored_tables)
    baseline_names = {name for name in baseline_names_all if name not in ignored_tables}
    python_names = {name for name in python_names_all if name not in ignored_tables}
    common_names = sorted(baseline_names & python_names)

    object_diffs: list[dict[str, object]] = []
    for object_name in common_names:
        baseline_type = baseline_objects[object_name]
        python_type = python_objects[object_name]
        baseline_columns = fetch_columns(connection, baseline_schema, object_name)
        python_columns = fetch_columns(connection, python_schema, object_name)
        baseline_row_count = fetch_row_count(connection, baseline_schema, object_name)
        python_row_count = fetch_row_count(connection, python_schema, object_name)

        content_hash_baseline = None
        content_hash_python = None
        content_equal = False
        if baseline_type == python_type and baseline_columns == python_columns:
            content_hash_baseline = fetch_content_hash(
                connection,
                baseline_schema,
                object_name,
                baseline_columns,
            )
            content_hash_python = fetch_content_hash(
                connection,
                python_schema,
                object_name,
                python_columns,
            )
            content_equal = content_hash_baseline == content_hash_python

        object_diff = {
            "name": object_name,
            "baseline_type": baseline_type,
            "python_type": python_type,
            "types_equal": baseline_type == python_type,
            "baseline_columns": baseline_columns,
            "python_columns": python_columns,
            "columns_equal": baseline_columns == python_columns,
            "baseline_row_count": baseline_row_count,
            "python_row_count": python_row_count,
            "row_count_equal": baseline_row_count == python_row_count,
            "baseline_content_hash": content_hash_baseline,
            "python_content_hash": content_hash_python,
            "content_equal": content_equal,
            "equal": (
                baseline_type == python_type
                and baseline_columns == python_columns
                and baseline_row_count == python_row_count
                and content_equal
            ),
        }
        if not object_diff["equal"]:
            object_diffs.append(object_diff)

    result["baseline_object_count"] = len(baseline_objects)
    result["python_object_count"] = len(python_objects)
    result["baseline_compared_object_count"] = len(baseline_names)
    result["python_compared_object_count"] = len(python_names)
    result["ignored_in_baseline"] = baseline_ignored
    result["ignored_in_python"] = python_ignored
    result["only_in_baseline"] = sorted(baseline_names - python_names)
    result["only_in_python"] = sorted(python_names - baseline_names)
    result["object_diffs"] = object_diffs
    result["equal"] = (
        not result["only_in_baseline"]
        and not result["only_in_python"]
        and not object_diffs
    )
    return result


def _canonicalize_artifact_bytes(data: bytes, dbname: str) -> bytes:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    normalized = text.replace("\r\n", "\n").replace(dbname, "<DBNAME>")
    return normalized.encode("utf-8")


def _artifact_digest(path: Path, dbname: str) -> str:
    data = path.read_bytes()
    normalized = _canonicalize_artifact_bytes(data, dbname)
    return hashlib.sha256(normalized).hexdigest()


def collect_artifacts(workdir: Path, dbname: str) -> dict[str, Path]:
    artifacts: dict[str, Path] = {}

    bif_path = workdir / f"Bif_{dbname}.xml"
    if bif_path.is_file():
        artifacts["Bif_<DBNAME>.xml"] = bif_path

    db_dir = workdir / dbname
    if db_dir.is_dir():
        for path in sorted(db_dir.rglob("*")):
            if path.is_file():
                relative_path = path.relative_to(db_dir).as_posix()
                artifacts[relative_path] = path

    return artifacts


def compare_artifacts(workdir: Path, baseline_dbname: str, python_dbname: str) -> dict[str, object]:
    baseline_artifacts = collect_artifacts(workdir, baseline_dbname)
    python_artifacts = collect_artifacts(workdir, python_dbname)
    baseline_names = set(baseline_artifacts)
    python_names = set(python_artifacts)
    common_names = sorted(baseline_names & python_names)

    file_diffs: list[dict[str, object]] = []
    for relative_name in common_names:
        baseline_path = baseline_artifacts[relative_name]
        python_path = python_artifacts[relative_name]
        baseline_hash = _artifact_digest(baseline_path, baseline_dbname)
        python_hash = _artifact_digest(python_path, python_dbname)
        if baseline_hash != python_hash:
            file_diffs.append(
                {
                    "path": relative_name,
                    "baseline_hash": baseline_hash,
                    "python_hash": python_hash,
                }
            )

    return {
        "baseline_artifact_count": len(baseline_artifacts),
        "python_artifact_count": len(python_artifacts),
        "only_in_baseline": sorted(baseline_names - python_names),
        "only_in_python": sorted(python_names - baseline_names),
        "file_diffs": file_diffs,
        "equal": not file_diffs and not (baseline_names - python_names) and not (python_names - baseline_names),
    }


def build_report(
    connection,
    workdir: Path,
    baseline_dbname: str,
    python_dbname: str,
    ignored_tables: set[str],
    mode_name: str,
) -> dict[str, object]:
    schema_reports = []
    for suffix in GENERATED_SCHEMA_SUFFIXES:
        schema_reports.append(
            compare_schema(
                connection,
                f"{baseline_dbname}{suffix}",
                f"{python_dbname}{suffix}",
                ignored_tables,
            )
        )

    artifact_report = compare_artifacts(workdir, baseline_dbname, python_dbname)
    overall_equal = all(schema_report["equal"] for schema_report in schema_reports) and artifact_report["equal"]
    return {
        "mode": mode_name,
        "baseline_dbname": baseline_dbname,
        "python_dbname": python_dbname,
        "generated_schema_suffixes": list(GENERATED_SCHEMA_SUFFIXES),
        "ignored_tables": sorted(ignored_tables),
        "schemas": schema_reports,
        "artifacts": artifact_report,
        "equal": overall_equal,
    }


def build_summary_lines(
    strict_report: dict[str, object],
    functional_report: dict[str, object],
    active_report: dict[str, object],
    host: str,
    port: int,
    user: str,
    outdir: Path,
) -> list[str]:
    lines = [
        f"Baseline dbname: {active_report['baseline_dbname']}",
        f"Python dbname: {active_report['python_dbname']}",
        f"MySQL endpoint: {host}:{port}",
        f"MySQL user: {user}",
        f"Strict equal: {strict_report['equal']}",
        (
            "Functional equal: "
            f"{functional_report['equal']} "
            f"(ignored tables: {', '.join(functional_report['ignored_tables']) or 'none'})"
        ),
        (
            "Active mode: "
            f"{active_report['mode']} "
            f"(active equal: {active_report['equal']}, "
            f"ignored tables: {', '.join(active_report['ignored_tables']) or 'none'})"
        ),
        "",
        "Schema comparison:",
    ]

    for schema_report in active_report["schemas"]:
        baseline_schema = schema_report["baseline_schema"]
        python_schema = schema_report["python_schema"]
        lines.append(
            f"- {baseline_schema} vs {python_schema}: equal={schema_report['equal']}, "
            f"baseline_exists={schema_report['baseline_exists']}, "
            f"python_exists={schema_report['python_exists']}"
        )
        if schema_report["baseline_exists"] and schema_report["python_exists"]:
            lines.append(
                f"  object_count baseline={schema_report['baseline_object_count']}, "
                f"python={schema_report['python_object_count']}, "
                f"compared baseline={schema_report['baseline_compared_object_count']}, "
                f"python={schema_report['python_compared_object_count']}, "
                f"ignored baseline={len(schema_report['ignored_in_baseline'])}, "
                f"python={len(schema_report['ignored_in_python'])}, "
                f"only_in_baseline={len(schema_report['only_in_baseline'])}, "
                f"only_in_python={len(schema_report['only_in_python'])}, "
                f"mismatched_objects={len(schema_report['object_diffs'])}"
            )

    artifact_report = active_report["artifacts"]
    lines.extend(
        [
            "",
            "Artifact comparison:",
            (
                f"- equal={artifact_report['equal']}, "
                f"baseline_artifacts={artifact_report['baseline_artifact_count']}, "
                f"python_artifacts={artifact_report['python_artifact_count']}, "
                f"only_in_baseline={len(artifact_report['only_in_baseline'])}, "
                f"only_in_python={len(artifact_report['only_in_python'])}, "
                f"mismatched_files={len(artifact_report['file_diffs'])}"
            ),
        ]
    )

    mismatch_samples: list[str] = []
    for schema_report in active_report["schemas"]:
        for object_name in schema_report.get("only_in_baseline", [])[:3]:
            mismatch_samples.append(
                f"only in baseline schema {schema_report['baseline_schema']}: {object_name}"
            )
        for object_name in schema_report.get("only_in_python", [])[:3]:
            mismatch_samples.append(
                f"only in python schema {schema_report['python_schema']}: {object_name}"
            )
        for object_diff in schema_report.get("object_diffs", [])[:3]:
            mismatch_samples.append(
                f"content/shape mismatch in {schema_report['baseline_schema']}::{object_diff['name']}"
            )
    for artifact_name in artifact_report["only_in_baseline"][:3]:
        mismatch_samples.append(f"only in baseline artifacts: {artifact_name}")
    for artifact_name in artifact_report["only_in_python"][:3]:
        mismatch_samples.append(f"only in python artifacts: {artifact_name}")
    for artifact_diff in artifact_report["file_diffs"][:3]:
        mismatch_samples.append(f"artifact content mismatch: {artifact_diff['path']}")

    if mismatch_samples:
        lines.append("")
        lines.append("First mismatches:")
        for sample in mismatch_samples[:12]:
            lines.append(f"- {sample}")

    lines.extend(
        [
            "",
            f"Summary written to: {outdir / 'summary.txt'}",
            f"Detailed report written to: {outdir / 'details.json'}",
        ]
    )
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Compare generated Java FactorBase and Python FactorBase outputs for two "
            "already-produced database names, including generated schemas and file artifacts."
        )
    )
    parser.add_argument("--baseline-dbname", required=True)
    parser.add_argument("--python-dbname", required=True)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--user", default=DEFAULT_USER)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    parser.add_argument("--workdir", type=Path, default=Path("."))
    parser.add_argument("--outdir", type=Path, default=Path("compare_runs/latest"))
    parser.add_argument(
        "--mode",
        choices=("strict", "functional"),
        default="strict",
        help="Comparison mode for the summary verdict. Strict compares everything. Functional ignores selected metadata tables.",
    )
    parser.add_argument(
        "--ignore-table",
        action="append",
        default=[],
        help="Extra table/view name to ignore in functional mode. May be passed multiple times.",
    )
    args = parser.parse_args()

    workdir = args.workdir.resolve()
    outdir = (workdir / args.outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    extra_ignored_tables = set(args.ignore_table)
    functional_ignored_tables = set(DEFAULT_FUNCTIONAL_IGNORE_TABLES) | extra_ignored_tables

    with connect(args.host, args.port, args.user, args.password) as connection:
        strict_report = build_report(
            connection=connection,
            workdir=workdir,
            baseline_dbname=args.baseline_dbname,
            python_dbname=args.python_dbname,
            ignored_tables=set(),
            mode_name="strict",
        )
        functional_report = build_report(
            connection=connection,
            workdir=workdir,
            baseline_dbname=args.baseline_dbname,
            python_dbname=args.python_dbname,
            ignored_tables=functional_ignored_tables,
            mode_name="functional",
        )

    active_report = strict_report if args.mode == "strict" else functional_report
    report = {
        "baseline_dbname": args.baseline_dbname,
        "python_dbname": args.python_dbname,
        "active_mode": args.mode,
        "default_functional_ignored_tables": list(DEFAULT_FUNCTIONAL_IGNORE_TABLES),
        "extra_ignored_tables": sorted(extra_ignored_tables),
        "strict": strict_report,
        "functional": functional_report,
        "active": active_report,
    }

    detail_path = outdir / "details.json"
    detail_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    summary_lines = build_summary_lines(
        strict_report,
        functional_report,
        active_report,
        args.host,
        args.port,
        args.user,
        outdir,
    )
    summary_path = outdir / "summary.txt"
    summary_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
    print("\n".join(summary_lines))


if __name__ == "__main__":
    main()
