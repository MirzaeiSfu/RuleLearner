from __future__ import annotations

import csv
import tempfile
from pathlib import Path

from .bn_client import run_bn_learner
from .bif_export import create_final_path_bayesnets, export_structure_bifs, generate_parameter_bif
from .config import FBConfig
from .db import connect, quote_identifier, use_database
from .fmt_pipeline import FMTPipeline
from .parameter_learning import run_parameter_learning
from .setup_pipeline import SetupPipeline
from .sql_runner import execute_sql_script


def _table_row_count(connection, schema_name: str, table_name: str) -> int:
    query = f"SELECT COUNT(*) FROM {quote_identifier(schema_name)}.{quote_identifier(table_name)}"
    with connection.cursor() as cursor:
        cursor.execute(query)
        row = cursor.fetchone()
    return int(row[0]) if row is not None else 0


def _table_exists(connection, schema_name: str, table_name: str) -> bool:
    query = (
        "SELECT COUNT(*) "
        "FROM information_schema.tables "
        "WHERE table_schema = %s AND table_name = %s"
    )
    with connection.cursor() as cursor:
        cursor.execute(query, (schema_name, table_name))
        row = cursor.fetchone()
    return bool(row and int(row[0]) > 0)


def _export_table_to_tsv(
    connection,
    schema_name: str,
    table_name: str,
    output_tsv: Path,
    where_clause: str = "MULT > 0",
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


def _write_edges_tsv(path: Path, edges: list[tuple[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["parent", "child"])
        for parent, child in edges:
            writer.writerow([parent, child])


def _read_edges_tsv(path: Path) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
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
            rows.append((parent, child))
    return rows


def _learn_edges_from_table(
    connection,
    config: FBConfig,
    jar_path: Path,
    table_name: str,
    temp_dir: Path,
    output_name_prefix: str,
    required_edges: list[tuple[str, str]] | None,
    forbidden_edges: list[tuple[str, str]] | None,
) -> list[tuple[str, str]]:
    input_tsv = temp_dir / f"{output_name_prefix}_input.tsv"
    output_edges = temp_dir / f"{output_name_prefix}_edges.tsv"
    required_path = temp_dir / f"{output_name_prefix}_required.tsv"
    forbidden_path = temp_dir / f"{output_name_prefix}_forbidden.tsv"

    _export_table_to_tsv(connection, config.ct_db, table_name, input_tsv, where_clause="MULT > 0")

    required_file = None
    if required_edges:
        _write_edges_tsv(required_path, required_edges)
        required_file = required_path

    forbidden_file = None
    if forbidden_edges:
        _write_edges_tsv(forbidden_path, forbidden_edges)
        forbidden_file = forbidden_path

    run_bn_learner(
        jar_path=jar_path,
        input_tsv=input_tsv,
        output_edges=output_edges,
        counts_column="MULT",
        discrete=not config.continuous,
        required_edges=required_file,
        forbidden_edges=forbidden_file,
    )

    return _read_edges_tsv(output_edges)


def _insert_entity_edges(connection, config: FBConfig, pvid: str, edges: list[tuple[str, str]]) -> None:
    if not edges:
        return
    query = (
        f"INSERT IGNORE INTO {quote_identifier(config.bn_db)}.Entity_BayesNets "
        "(pvid, child, parent) VALUES (%s, %s, %s)"
    )
    payload = [(pvid, child, parent) for parent, child in edges]
    with connection.cursor() as cursor:
        cursor.executemany(query, payload)
    connection.commit()


def _insert_path_edges(connection, config: FBConfig, rchain_id: str, edges: list[tuple[str, str]]) -> None:
    if not edges:
        return
    query = (
        f"INSERT IGNORE INTO {quote_identifier(config.bn_db)}.Path_BayesNets "
        "(Rchain, child, parent) VALUES (%s, %s, %s)"
    )
    payload = [(rchain_id, child, parent) for parent, child in edges]
    with connection.cursor() as cursor:
        cursor.executemany(query, payload)
    connection.commit()


def _delete_forbidden_path_edges(connection, config: FBConfig, rchain_id: str) -> None:
    query = (
        f"DELETE FROM {quote_identifier(config.bn_db)}.Path_BayesNets "
        "WHERE Rchain = %s "
        "AND (child, parent) IN ("
        f"  SELECT child, parent FROM {quote_identifier(config.bn_db)}.Path_Forbidden_Edges "
        "  WHERE Rchain = %s"
        ")"
    )
    with connection.cursor() as cursor:
        cursor.execute(query, (rchain_id, rchain_id))
    connection.commit()


def _learn_entity_bayesnets(
    connection,
    config: FBConfig,
    jar_path: Path,
    temp_dir: Path,
) -> None:
    pvariables_query = f"SELECT pvid FROM {quote_identifier(config.bn_db)}.PVariables ORDER BY pvid"

    with connection.cursor() as cursor:
        cursor.execute(pvariables_query)
        pvid_rows = cursor.fetchall()

    for row in pvid_rows:
        pvid = str(row[0])
        counts_table = f"{pvid}_counts"
        if not _table_exists(connection, config.ct_db, counts_table):
            raise RuntimeError(
                (
                    f"Expected table '{config.ct_db}.{counts_table}' not found. "
                    "The counterpart flow expects CT/count tables to exist before BN learning."
                )
            )
        tuple_count = _table_row_count(connection, config.ct_db, counts_table)

        if tuple_count > 1:
            edges = _learn_edges_from_table(
                connection=connection,
                config=config,
                jar_path=jar_path,
                table_name=counts_table,
                temp_dir=temp_dir,
                output_name_prefix=f"pvar_{pvid.replace(',', '_')}",
                required_edges=None,
                forbidden_edges=None,
            )
            _insert_entity_edges(connection, config, pvid, edges)
            continue

        single_node_query = (
            "SELECT nodes.`1nid` "
            f"FROM {quote_identifier(config.bn_db)}.`1Nodes` AS nodes, "
            f"{quote_identifier(config.setup_db)}.`EntityTables` AS entity_tables "
            "WHERE nodes.pvid = CONCAT(entity_tables.Table_name, '0') "
            "AND nodes.pvid = %s"
        )
        with connection.cursor() as cursor:
            cursor.execute(single_node_query, (pvid,))
            children = [str(item[0]) for item in cursor.fetchall()]

        default_edges = [("", child) for child in children]
        _insert_entity_edges(connection, config, pvid, default_edges)


def _get_lattice_height(connection, config: FBConfig) -> int:
    query = f"SELECT COALESCE(MAX(length), 0) FROM {quote_identifier(config.bn_db)}.lattice_set"
    with connection.cursor() as cursor:
        cursor.execute(query)
        row = cursor.fetchone()
    return int(row[0]) if row is not None else 0


def _get_rchain_ids_for_length(connection, config: FBConfig, length: int) -> list[str]:
    query = (
        f"SELECT name FROM {quote_identifier(config.bn_db)}.lattice_set "
        "WHERE length = %s ORDER BY name"
    )
    with connection.cursor() as cursor:
        cursor.execute(query, (length,))
        rows = cursor.fetchall()
    return [str(row[0]) for row in rows]


def _fetch_guidance_edges(
    connection,
    config: FBConfig,
    table_name: str,
    rchain_ids: list[str],
) -> list[tuple[str, str]]:
    if not rchain_ids:
        return []
    placeholders = ", ".join(["%s"] * len(rchain_ids))
    query = (
        f"SELECT parent, child FROM {quote_identifier(config.bn_db)}.{quote_identifier(table_name)} "
        f"WHERE Rchain IN ({placeholders})"
    )
    with connection.cursor() as cursor:
        cursor.execute(query, tuple(rchain_ids))
        rows = cursor.fetchall()
    return [("" if row[0] is None else str(row[0]), "" if row[1] is None else str(row[1])) for row in rows]


def _get_short_rnid(connection, config: FBConfig, rchain_id: str) -> str:
    query = (
        f"SELECT short_rnid FROM {quote_identifier(config.bn_db)}.lattice_mapping "
        "WHERE orig_rnid = %s"
    )
    with connection.cursor() as cursor:
        cursor.execute(query, (rchain_id,))
        row = cursor.fetchone()
    if row is None:
        raise RuntimeError(f"No short_rnid found for rchain '{rchain_id}'.")
    return str(row[0])


def _link_analysis_off_specific_propagation(connection, config: FBConfig, height: int) -> None:
    setup_db = quote_identifier(config.setup_db)
    queries = [
        (
            "INSERT IGNORE INTO InheritedEdges "
            "SELECT DISTINCT lattice_rel.child AS Rchain, Path_BayesNets.child AS child, Path_BayesNets.parent AS parent "
            f"FROM Path_BayesNets, {setup_db}.lattice_rel, {setup_db}.lattice_set "
            "WHERE lattice_rel.parent = Path_BayesNets.Rchain "
            "AND Path_BayesNets.parent <> '' "
            "AND lattice_set.name = lattice_rel.parent "
            f"AND lattice_set.length = {height} "
            "ORDER BY Rchain"
        ),
        (
            "INSERT IGNORE INTO NewLearnedEdges "
            "SELECT Path_BayesNets.Rchain, Path_BayesNets.child, Path_BayesNets.parent "
            f"FROM Path_BayesNets, {setup_db}.lattice_set "
            "WHERE Path_BayesNets.parent <> '' "
            "AND Path_BayesNets.Rchain = lattice_set.name "
            f"AND lattice_set.length = {height} "
            "AND (Path_BayesNets.Rchain, Path_BayesNets.child, Path_BayesNets.parent) NOT IN ("
            "SELECT * FROM Path_Required_Edges"
            ")"
        ),
        (
            "INSERT IGNORE INTO InheritedEdges "
            "SELECT DISTINCT NewLearnedEdges.Rchain AS Rchain, NewLearnedEdges.child AS child, lattice_membership.member AS parent "
            f"FROM NewLearnedEdges, {setup_db}.lattice_membership "
            "WHERE NewLearnedEdges.Rchain = lattice_membership.name"
        ),
        (
            "INSERT IGNORE INTO Path_BayesNets "
            "SELECT * FROM InheritedEdges"
        ),
    ]

    with connection.cursor() as cursor:
        for query in queries:
            cursor.execute(query)
    connection.commit()


def _propagate_edge_information(connection, config: FBConfig, height: int) -> None:
    setup_db = quote_identifier(config.setup_db)

    queries = [
        (
            "INSERT IGNORE INTO InheritedEdges "
            "SELECT DISTINCT lattice_rel.child AS Rchain, Path_BayesNets.child AS child, Path_BayesNets.parent AS parent "
            f"FROM Path_BayesNets, {setup_db}.lattice_rel, {setup_db}.lattice_set "
            "WHERE lattice_rel.parent = Path_BayesNets.Rchain "
            "AND Path_BayesNets.parent <> '' "
            "AND lattice_set.name = lattice_rel.parent "
            f"AND lattice_set.length = {height} "
            "ORDER BY Rchain"
        ),
        (
            "INSERT IGNORE INTO Path_Required_Edges "
            "SELECT DISTINCT Rchain, child, parent "
            f"FROM InheritedEdges, {setup_db}.lattice_set "
            "WHERE Rchain = lattice_set.name "
            f"AND lattice_set.length = {height + 1} "
            "AND (Rchain, parent, child) NOT IN (SELECT * FROM InheritedEdges) "
            f"AND child NOT IN (SELECT rnid FROM {setup_db}.RNodes)"
        ),
        (
            "INSERT IGNORE INTO Path_Complement_Edges "
            "SELECT DISTINCT BN_nodes1.Rchain AS Rchain, BN_nodes1.node AS child, BN_nodes2.node AS parent "
            f"FROM Path_BN_nodes AS BN_nodes1, Path_BN_nodes AS BN_nodes2, {setup_db}.lattice_set "
            "WHERE lattice_set.name = BN_nodes1.Rchain "
            f"AND lattice_set.length = {height} "
            "AND BN_nodes1.Rchain = BN_nodes2.Rchain "
            "AND NOT EXISTS ("
            "SELECT * FROM Path_BayesNets "
            "WHERE Path_BayesNets.Rchain = BN_nodes1.Rchain "
            "AND Path_BayesNets.child = BN_nodes1.node "
            "AND Path_BayesNets.parent = BN_nodes2.node"
            ")"
        ),
        (
            "INSERT IGNORE INTO Path_Forbidden_Edges "
            "SELECT DISTINCT lattice_rel.child AS Rchain, Path_Complement_Edges.child AS child, Path_Complement_Edges.parent AS parent "
            f"FROM Path_Complement_Edges, {setup_db}.lattice_rel, {setup_db}.lattice_set "
            "WHERE lattice_set.name = lattice_rel.parent "
            f"AND lattice_set.length = {height} "
            "AND lattice_rel.parent = Path_Complement_Edges.Rchain "
            "AND Path_Complement_Edges.parent <> '' "
            "AND (lattice_rel.child, Path_Complement_Edges.child, Path_Complement_Edges.parent) NOT IN ("
            "SELECT Rchain, child, parent FROM Path_Required_Edges"
            ") "
            f"AND Path_Complement_Edges.parent NOT IN (SELECT rnid FROM {setup_db}.RNodes)"
        ),
    ]

    with connection.cursor() as cursor:
        cursor.execute(queries[0])
    connection.commit()

    if not config.link_correlations:
        _link_analysis_off_specific_propagation(connection, config, height)

    with connection.cursor() as cursor:
        for query in queries[1:]:
            cursor.execute(query)
    connection.commit()


def _propagate_context_edges(connection, config: FBConfig, max_number_of_members: int) -> None:
    setup_db = quote_identifier(config.setup_db)
    with connection.cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS RNodeEdges")
        cursor.execute("CREATE TABLE RNodeEdges LIKE Path_BayesNets")
        cursor.execute("DROP TABLE IF EXISTS ContextEdges")
        cursor.execute(
            "CREATE TABLE ContextEdges AS "
            "SELECT DISTINCT NewLearnedEdges.Rchain AS Rchain, NewLearnedEdges.child AS child, lattice_membership.member AS parent "
            f"FROM NewLearnedEdges, {setup_db}.lattice_membership "
            "WHERE NewLearnedEdges.Rchain = lattice_membership.name"
        )
        cursor.execute(
            "INSERT IGNORE INTO RNodeEdges "
            "SELECT Rchain, child, parent "
            "FROM ContextEdges, lattice_set "
            "WHERE lattice_set.name = ContextEdges.Rchain "
            "AND lattice_set.length = 1"
        )

        for length in range(2, max_number_of_members + 1):
            cursor.execute(
                "INSERT IGNORE INTO RNodeEdges "
                "SELECT Rchain, child, parent "
                "FROM ContextEdges, lattice_set "
                "WHERE lattice_set.name = ContextEdges.Rchain "
                f"AND lattice_set.length = {length} "
                "UNION "
                "SELECT DISTINCT lattice_rel.child, RNodeEdges.child, RNodeEdges.parent "
                "FROM lattice_set, lattice_rel, RNodeEdges "
                f"WHERE lattice_set.length = {length} "
                "AND lattice_rel.child = lattice_set.name "
                "AND RNodeEdges.Rchain = lattice_rel.parent"
            )

        cursor.execute("INSERT IGNORE INTO Path_BayesNets SELECT * FROM RNodeEdges")

        cursor.execute(
            "SELECT name AS Rchain FROM lattice_set "
            "WHERE length = (SELECT MAX(length) FROM lattice_set) LIMIT 1"
        )
        row = cursor.fetchone()
        if row is None:
            connection.commit()
            return
        largest_rchain = str(row[0])

        cursor.execute(
            "SELECT name AS RChain FROM lattice_set WHERE lattice_set.length = 1"
        )
        rnode_ids = [str(item[0]) for item in cursor.fetchall()]

        insert_query = (
            "INSERT IGNORE INTO Path_BayesNets "
            "(SELECT %s AS Rchain, %s AS child, '' AS parent)"
        )
        for rnode_id in rnode_ids:
            cursor.execute(insert_query, (largest_rchain, rnode_id))
    connection.commit()


def _insert_missing_fids_as_children(connection, largest_rchain: str) -> None:
    query = (
        "INSERT IGNORE INTO Path_BayesNets "
        "SELECT %s AS Rchain, Fid AS child, '' AS parent "
        "FROM FNodes "
        "WHERE Fid NOT IN ("
        "SELECT DISTINCT child FROM Path_BayesNets WHERE Rchain = %s"
        ")"
    )
    with connection.cursor() as cursor:
        cursor.execute(query, (largest_rchain, largest_rchain))
    connection.commit()


def _learn_rchain_bayesnets(
    connection,
    config: FBConfig,
    jar_path: Path,
    max_height: int,
    temp_dir: Path,
) -> None:
    for length in range(1, max_height + 1):
        rchain_ids = _get_rchain_ids_for_length(connection, config, length)
        if not rchain_ids:
            continue

        required_edges = _fetch_guidance_edges(connection, config, "Path_Required_Edges", rchain_ids)
        forbidden_edges = _fetch_guidance_edges(connection, config, "Path_Forbidden_Edges", rchain_ids)

        for rchain_id in rchain_ids:
            short_rnid = _get_short_rnid(connection, config, rchain_id)
            ct_table_name = f"{short_rnid}_CT"
            if not _table_exists(connection, config.ct_db, ct_table_name):
                raise RuntimeError(
                    (
                        f"Expected table '{config.ct_db}.{ct_table_name}' not found. "
                        "The counterpart flow expects CT tables to exist before RChain learning."
                    )
                )
            tuple_count = _table_row_count(connection, config.ct_db, ct_table_name)

            if tuple_count <= 1:
                continue

            edges = _learn_edges_from_table(
                connection=connection,
                config=config,
                jar_path=jar_path,
                table_name=ct_table_name,
                temp_dir=temp_dir,
                output_name_prefix=f"rchain_{length}_{short_rnid.replace(',', '_')}",
                required_edges=required_edges,
                forbidden_edges=forbidden_edges,
            )

            _insert_path_edges(connection, config, rchain_id, edges)
            _delete_forbidden_path_edges(connection, config, rchain_id)

        _propagate_edge_information(connection, config, length)


def run_factorbase_counterpart(
    config: FBConfig,
    scripts_dir: Path,
    jar_path: Path,
    drop_run_metadata: bool = True,
) -> None:
    if config.automatic_setup:
        SetupPipeline(
            config=config,
            scripts_dir=scripts_dir,
            drop_run_metadata=drop_run_metadata,
        ).run()

    FMTPipeline(config).run()

    connection = connect(config, database=config.bn_db)
    try:
        use_database(connection, config.bn_db)

        with tempfile.TemporaryDirectory(prefix=f"pyfactorbase_{config.dbname}_") as temp_dir_str:
            temp_dir = Path(temp_dir_str)
            _learn_entity_bayesnets(connection, config, jar_path, temp_dir)
            execute_sql_script(
                connection,
                scripts_dir / "modelmanager_populate.sql",
                config.dbname,
                config.dbcollation,
            )
            max_height = _get_lattice_height(connection, config)
            _learn_rchain_bayesnets(connection, config, jar_path, max_height, temp_dir)
            _propagate_context_edges(connection, config, max_height)

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT name FROM lattice_set WHERE length = (SELECT MAX(length) FROM lattice_set) LIMIT 1"
            )
            row = cursor.fetchone()
        if row is None:
            return
        largest_rchain = str(row[0])

        _insert_missing_fids_as_children(connection, largest_rchain)
        create_final_path_bayesnets(connection, largest_rchain)
        export_structure_bifs(connection, config, max_height)

        if not config.continuous and not config.skip_parameter_learning:
            run_parameter_learning(connection, config)
            generate_parameter_bif(connection, config)
    finally:
        connection.close()
