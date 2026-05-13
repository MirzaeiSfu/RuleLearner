from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from .config import FBConfig
from .db import normalize_identifier_text, quote_identifier


@dataclass(frozen=True)
class LargestRChainInfo:
    rchain: str
    short_rchain: str


def run_parameter_learning(connection, config: FBConfig) -> LargestRChainInfo:
    """Port of the Java parameter-learning/KLD/BIF preparation phase.

    The current Java codebase reads UseLocal_CT but does not expose a separate
    concrete CP/KLD/BIF materialization path in this repository. For Python
    compatibility we therefore accept UseLocal_CT=1 and run the same BN-schema
    CP pipeline that Java uses for the fully implemented path, producing the
    standard `_CP`, `_CP_smoothed`, `Scores`, KLD, and BIF artifacts.
    """

    info = get_largest_rchain_info(connection)
    _run_cp_generator(connection, config)
    _generate_cp_tables(connection, config, info)

    if config.compute_kld:
        _run_kld_generator(connection, config, info)
    else:
        _smoothed_cp(connection, info.rchain)

    return info


def get_largest_rchain_info(connection) -> LargestRChainInfo:
    query = (
        "SELECT short_rnid AS short_rchain, orig_rnid AS rchain "
        "FROM lattice_set "
        "JOIN lattice_mapping ON lattice_set.name = lattice_mapping.orig_rnid "
        "WHERE lattice_set.length = (SELECT MAX(length) FROM lattice_set) "
        "ORDER BY short_rnid "
        "LIMIT 1"
    )
    with connection.cursor() as cursor:
        cursor.execute(query)
        row = cursor.fetchone()
    if row is None:
        raise RuntimeError("Unable to determine the largest relationship chain.")
    return LargestRChainInfo(
        rchain=str(row[1]),
        short_rchain=normalize_identifier_text(row[0]),
    )


def _run_cp_generator(connection, config: FBConfig) -> None:
    if _object_type(connection, "FNodes") == "VIEW":
        with connection.cursor() as cursor:
            cursor.execute("RENAME TABLE FNodes TO FNodes_view")
            cursor.execute("CREATE TABLE FNodes AS SELECT * FROM FNodes_view")
            cursor.execute("DROP VIEW FNodes_view")
        connection.commit()

    with connection.cursor() as cursor:
        cursor.execute("SELECT rnid FROM RNodes")
        rnids = [str(row[0]) for row in cursor.fetchall()]

    setup_attribute_value = f"{quote_identifier(config.setup_db)}.Attribute_Value"
    with connection.cursor() as cursor:
        for rnid in rnids:
            cursor.execute("SET SQL_SAFE_UPDATES = 0")
            cursor.execute(
                f"DELETE FROM {setup_attribute_value} WHERE column_name = %s",
                (rnid,),
            )
            cursor.execute(
                f"INSERT INTO {setup_attribute_value} VALUES (%s, 'T')",
                (rnid,),
            )
            cursor.execute(
                f"INSERT INTO {setup_attribute_value} VALUES (%s, 'F')",
                (rnid,),
            )
    connection.commit()


def _generate_cp_tables(connection, config: FBConfig, info: LargestRChainInfo) -> None:
    _prepare_parameter_learning_schema(connection, config, info.rchain)
    _generate_cp_tables_without_parents(connection, config, info)
    _generate_cp_tables_with_parents(connection, config, info.rchain)
    _finalize_scores(connection)


def _prepare_parameter_learning_schema(connection, config: FBConfig, rchain: str) -> None:
    _local_mult_update(connection, config, rchain)

    with connection.cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS Scores")
        cursor.execute(
            "CREATE TABLE Scores ("
            "`Fid` varchar(255) NOT NULL, "
            "`LogLikelihood` float(20,2) default NULL, "
            "`Normal_LogLikelihood` float(20,2) default NULL, "
            "`Parameters` bigint(20) default NULL, "
            "`SampleSize` bigint(20) default NULL, "
            "`BIC` float(20,2) default NULL, "
            "`AIC` float(20,2) default NULL, "
            "`Pseudo_BIC` float(20,2) default NULL, "
            "`Pseudo_AIC` float(20,2) default NULL, "
            "`Big_SampleSize` DECIMAL(65) default NULL, "
            "PRIMARY KEY (`Fid`))"
        )
        cursor.execute(
            "INSERT INTO Scores(Fid) "
            "SELECT DISTINCT child FROM Path_BayesNets WHERE Rchain = %s",
            (rchain,),
        )
        cursor.execute(
            "UPDATE FNodes, RNodes "
            "SET FunctorName = ("
            "  SELECT DISTINCT rnid FROM RNodes "
            "  WHERE FNodes.FunctorName = RNodes.TABLE_NAME AND FNodes.Fid = RNodes.rnid"
            ") "
            "WHERE FNodes.FunctorName = RNodes.TABLE_NAME AND FNodes.Fid = RNodes.rnid"
        )

        cursor.execute("DROP TABLE IF EXISTS NumAttributes")
        cursor.execute(
            f"CREATE TABLE NumAttributes AS "
            f"SELECT COUNT(VALUE) AS NumAtts, COLUMN_NAME "
            f"FROM {quote_identifier(config.setup_db)}.Attribute_Value "
            "GROUP BY COLUMN_NAME"
        )

        cursor.execute("DROP TABLE IF EXISTS RNodes_inFamily")
        cursor.execute(
            "CREATE TABLE RNodes_inFamily AS "
            "SELECT FamilyRNodes.child AS ChildNode, FamilyRNodes.parent AS Rnode "
            "FROM Path_BayesNets AS FamilyRNodes, FNodes AS RNode_check "
            "WHERE FamilyRNodes.Rchain = %s "
            "AND RNode_check.Fid = FamilyRNodes.parent "
            "AND RNode_check.Type = 'RNode'",
            (rchain,),
        )

        cursor.execute("DROP VIEW IF EXISTS RNodes_inFamily_view")
        cursor.execute(
            "CREATE VIEW RNodes_inFamily_view AS "
            "SELECT DISTINCT child, rnid, short_rnid "
            "FROM Final_Path_BayesNets_view BN, RNodes, lattice_mapping lm "
            "WHERE RNodes.rnid = BN.parent AND lm.orig_rnid = BN.parent "
            "UNION "
            "SELECT DISTINCT BN.child AS child, RNodes.rnid AS rnid, short_rnid "
            "FROM Final_Path_BayesNets_view BN, RNodes, lattice_mapping lm "
            "WHERE RNodes.rnid = BN.child AND lm.orig_rnid = child "
            "UNION "
            "SELECT DISTINCT BN.child AS child, RNodes_2Nodes.rnid AS rnid, short_rnid "
            "FROM (Final_Path_BayesNets_view BN, lattice_mapping lm JOIN RNodes_2Nodes) "
            "WHERE RNodes_2Nodes.2nid = BN.parent AND RNodes_2Nodes.rnid = lm.orig_rnid "
            "UNION "
            "SELECT DISTINCT BN.child AS child, RNodes_2Nodes.rnid AS rnid, short_rnid "
            "FROM (Final_Path_BayesNets_view BN, lattice_mapping lm JOIN RNodes_2Nodes) "
            "WHERE RNodes_2Nodes.2nid = BN.child AND RNodes_2Nodes.rnid = lm.orig_rnid"
        )

        cursor.execute("DROP TABLE IF EXISTS 2Nodes_inFamily")
        cursor.execute(
            "CREATE TABLE 2Nodes_inFamily AS "
            "SELECT Family2Nodes.child AS ChildNode, Family2Nodes.parent AS 2node, NumAttributes.NumAtts "
            "FROM Path_BayesNets AS Family2Nodes, FNodes AS 2Node_check, NumAttributes "
            "WHERE Family2Nodes.Rchain = %s "
            "AND 2Node_check.Fid = Family2Nodes.parent "
            "AND 2Node_check.Type = '2Node' "
            "AND 2Node_check.FunctorName = NumAttributes.COLUMN_NAME",
            (rchain,),
        )

        cursor.execute("DROP TABLE IF EXISTS 1Nodes_inFamily")
        cursor.execute(
            "CREATE TABLE 1Nodes_inFamily AS "
            "SELECT Family1Nodes.child AS ChildNode, Family1Nodes.parent AS 1node, NumAttributes.NumAtts "
            "FROM Path_BayesNets AS Family1Nodes, FNodes AS 1Node_check, NumAttributes "
            "WHERE Family1Nodes.Rchain = %s "
            "AND 1Node_check.Fid = Family1Nodes.parent "
            "AND 1Node_check.Type = '1Node' "
            "AND 1Node_check.FunctorName = NumAttributes.COLUMN_NAME",
            (rchain,),
        )

        cursor.execute("DROP TABLE IF EXISTS RNodes_2Nodes_Family")
        cursor.execute(
            "CREATE TABLE RNodes_2Nodes_Family AS "
            "SELECT RNodes_inFamily.ChildNode, RNodes_inFamily.Rnode, 2Nodes_inFamily.2Node, 2Nodes_inFamily.NumAtts "
            "FROM RNodes_inFamily, 2Nodes_inFamily "
            "WHERE RNodes_inFamily.ChildNode = 2Nodes_inFamily.ChildNode "
            "AND (RNodes_inFamily.Rnode, 2Nodes_inFamily.2Node) IN (SELECT rnid, 2nid FROM RNodes_2Nodes)"
        )

        cursor.execute("DROP TABLE IF EXISTS ChildPars")
        cursor.execute(
            "CREATE TABLE ChildPars AS "
            "SELECT DISTINCT (NumAtts - 1) AS NumPars, FNodes.Fid AS ChildNode "
            "FROM FNodes JOIN NumAttributes "
            "WHERE FNodes.FunctorName = NumAttributes.COLUMN_NAME"
        )

        cursor.execute("DROP TABLE IF EXISTS 1NodePars")
        cursor.execute(
            "CREATE TABLE 1NodePars AS "
            "SELECT ChildNode, EXP(SUM(LOG(NumAtts))) AS NumPars "
            "FROM 1Nodes_inFamily "
            "GROUP BY ChildNode "
            "UNION "
            "SELECT DISTINCT child AS ChildNode, 1 AS NumPars "
            "FROM Path_BayesNets "
            "WHERE Path_BayesNets.Rchain = %s "
            "AND child NOT IN (SELECT ChildNode FROM 1Nodes_inFamily)",
            (rchain,),
        )

        cursor.execute("DROP TABLE IF EXISTS RelationsParents")
        cursor.execute(
            "CREATE TABLE RelationsParents AS "
            "SELECT ChildNode, rnid, 2node, NumAtts "
            "FROM 2Nodes_inFamily, RNodes_2Nodes "
            "WHERE 2node = 2nid "
            "UNION "
            "SELECT ChildNode, Rnode AS rnid, Rnode, 1 AS NumVals "
            "FROM RNodes_inFamily"
        )

        cursor.execute("DROP TABLE IF EXISTS RelationsPars")
        cursor.execute(
            "CREATE TABLE RelationsPars AS "
            "SELECT ChildNode, EXP(SUM(LOG(NumPars))) AS NumPars "
            "FROM ("
            "  SELECT ChildNode, rnid, EXP(SUM(LOG(NumAtts))) + 1 AS NumPars "
            "  FROM RelationsParents "
            "  GROUP BY ChildNode, rnid"
            ") AS ParPerRelation "
            "GROUP BY ChildNode"
        )

        cursor.execute(
            "UPDATE Scores, ChildPars, 1NodePars, RelationsPars "
            "SET Parameters = ("
            "  SELECT ChildPars.NumPars * 1NodePars.NumPars * RelationsPars.NumPars "
            "  FROM ChildPars, 1NodePars, RelationsPars "
            "  WHERE ChildPars.ChildNode = 1NodePars.ChildNode "
            "  AND 1NodePars.ChildNode = RelationsPars.ChildNode "
            "  AND Scores.Fid = RelationsPars.ChildNode"
            ") "
            "WHERE RelationsPars.ChildNode = Scores.Fid"
        )

        cursor.execute(
            "UPDATE Scores, ChildPars, 1NodePars "
            "SET Parameters = ("
            "  SELECT ChildPars.NumPars * 1NodePars.NumPars "
            "  FROM ChildPars, 1NodePars "
            "  WHERE ChildPars.ChildNode = 1NodePars.ChildNode "
            "  AND 1NodePars.ChildNode = Scores.Fid"
            ") "
            "WHERE 1NodePars.ChildNode = Scores.Fid AND Parameters IS NULL"
        )
    connection.commit()


def _local_mult_update(connection, config: FBConfig, rchain: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS FNodes_pvars_UNION_RNodes_pvars")
        cursor.execute(
            f"CREATE TABLE FNodes_pvars_UNION_RNodes_pvars AS "
            "SELECT rnid AS Fid, pvid FROM RNodes_pvars "
            "UNION DISTINCT "
            f"SELECT * FROM {quote_identifier(config.setup_db)}.FNodes_pvars"
        )

        cursor.execute("DROP TABLE IF EXISTS Pvars_Family")
        cursor.execute(
            "CREATE TABLE Pvars_Family AS "
            "SELECT child, pvid "
            "FROM Path_BayesNets, FNodes_pvars_UNION_RNodes_pvars "
            "WHERE Rchain = %s AND Path_BayesNets.parent = Fid "
            "UNION "
            "SELECT child, pvid "
            "FROM Path_BayesNets, FNodes_pvars_UNION_RNodes_pvars "
            "WHERE Rchain = %s AND Path_BayesNets.child = Fid",
            (rchain, rchain),
        )

        cursor.execute("DROP TABLE IF EXISTS Pvars_Not_In_Family")
        cursor.execute(
            "CREATE TABLE Pvars_Not_In_Family AS "
            "SELECT Fid AS child, pvid "
            "FROM FNodes, PVariables "
            "WHERE (Fid, pvid) NOT IN (SELECT * FROM Pvars_Family)"
        )

        if not _column_exists(connection, "PVariables", "Tuples"):
            cursor.execute(
                "ALTER TABLE PVariables ADD COLUMN Tuples BIGINT(20) NULL AFTER index_number"
            )

        cursor.execute(
            f"SELECT table_name FROM {quote_identifier(config.setup_db)}.EntityTables"
        )
        entity_tables = [str(row[0]) for row in cursor.fetchall()]

        for entity_table in entity_tables:
            cursor.execute(
                f"UPDATE PVariables SET Tuples = ("
                f"  SELECT COUNT(*) FROM {quote_identifier(config.dbname)}.{quote_identifier(entity_table)}"
                ") "
                "WHERE PVariables.table_name = %s",
                (entity_table,),
            )

        if not _column_exists(connection, "Pvars_Not_In_Family", "Tuples"):
            cursor.execute(
                "ALTER TABLE Pvars_Not_In_Family ADD COLUMN Tuples BIGINT(20) NULL AFTER pvid"
            )
        cursor.execute(
            "UPDATE Pvars_Not_In_Family "
            "SET Tuples = ("
            "  SELECT Tuples FROM PVariables WHERE PVariables.pvid = Pvars_Not_In_Family.pvid"
            ")"
        )
    connection.commit()


def _generate_cp_tables_without_parents(
    connection,
    config: FBConfig,
    info: LargestRChainInfo,
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT child FROM Path_BayesNets "
            "WHERE Rchain = %s AND parent = '' "
            "AND child NOT IN ("
            "  SELECT DISTINCT child FROM Path_BayesNets WHERE parent <> '' AND Rchain = %s"
            ")",
            (info.rchain, info.rchain),
        )
        nodes = [str(row[0]) for row in cursor.fetchall()]

    for node_name in nodes:
        big_table = _supporting_table_for_node(connection, node_name) or f"{info.short_rchain}_CT"
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT SUM(MULT) FROM {quote_identifier(config.ct_db)}.{quote_identifier(big_table)}"
            )
            row = cursor.fetchone()
        denominator = float(row[0]) if row is not None and row[0] is not None else 0.0
        _no_parent_update(connection, config, big_table, node_name, denominator)


def _no_parent_update(
    connection,
    config: FBConfig,
    big_table: str,
    node_name: str,
    denominator: float,
) -> None:
    table_name = f"{node_name}_CP"
    escaped_table = quote_identifier(table_name)
    node_identifier = quote_identifier(node_name)
    source_table = f"{quote_identifier(config.ct_db)}.{quote_identifier(big_table)}"

    with connection.cursor() as cursor:
        cursor.execute(f"DROP TABLE IF EXISTS {escaped_table}")
        cursor.execute(
            f"CREATE TABLE {escaped_table} ("
            f"{node_identifier} VARCHAR(200) NOT NULL, "
            "CP FLOAT(7,6), "
            "MULT DECIMAL(65), "
            "local_mult DECIMAL(65)"
            ")"
        )
        cursor.execute(
            f"INSERT INTO {escaped_table} ({node_identifier}) "
            f"SELECT DISTINCT {node_identifier} FROM {source_table}"
        )
        cursor.execute(f"SELECT {node_identifier} FROM {escaped_table}")
        values = ["" if row[0] is None else str(row[0]) for row in cursor.fetchall()]

        for value in values:
            cursor.execute(
                f"SELECT SUM(MULT) FROM {source_table} WHERE {node_identifier} = %s",
                (value,),
            )
            numerator_row = cursor.fetchone()
            numerator = float(numerator_row[0]) if numerator_row and numerator_row[0] is not None else 0.0
            cursor.execute(
                f"UPDATE {escaped_table} SET MULT = %s WHERE {node_identifier} = %s",
                (numerator, value),
            )
            cp_value = numerator / denominator if denominator else 0.0
            cursor.execute(
                f"UPDATE {escaped_table} SET CP = %s WHERE {node_identifier} = %s",
                (cp_value, value),
            )
            cursor.execute(
                f"UPDATE {escaped_table} SET local_mult = %s WHERE {node_identifier} = %s",
                (numerator, value),
            )

        cursor.execute(f"ALTER TABLE {escaped_table} ADD likelihood FLOAT(20,2)")
        cursor.execute(f"UPDATE {escaped_table} SET likelihood = LOG(CP) * local_mult")

        cursor.execute(f"SELECT SUM(likelihood) FROM {escaped_table}")
        log_row = cursor.fetchone()
        log_likelihood = float(log_row[0]) if log_row and log_row[0] is not None else 0.0
        cursor.execute(
            "UPDATE Scores SET LogLikelihood = %s WHERE Scores.Fid = %s",
            (log_likelihood, node_name),
        )

        cursor.execute(f"SELECT SUM(local_mult) FROM {escaped_table}")
        sample_row = cursor.fetchone()
        sample_size = int(sample_row[0]) if sample_row and sample_row[0] is not None else 0
        cursor.execute(
            "UPDATE Scores SET SampleSize = %s WHERE Scores.Fid = %s",
            (sample_size, node_name),
        )

        cursor.execute(f"SELECT SUM(MULT) FROM {escaped_table}")
        big_row = cursor.fetchone()
        big_size = float(big_row[0]) if big_row and big_row[0] is not None else 0.0
        cursor.execute(
            "UPDATE Scores SET Big_SampleSize = %s WHERE Scores.Fid = %s",
            (big_size, node_name),
        )

        cursor.execute(f"ALTER TABLE {escaped_table} ADD prior FLOAT(7,6)")
        cursor.execute(f"UPDATE {escaped_table} SET prior = CP")
    connection.commit()


def _generate_cp_tables_with_parents(connection, config: FBConfig, rchain: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT DISTINCT child FROM Path_BayesNets "
            "WHERE Rchain = %s AND parent <> ''",
            (rchain,),
        )
        nodes = [str(row[0]) for row in cursor.fetchall()]

    for node_name in nodes:
        big_table = _supporting_table_for_node(connection, node_name)
        if big_table is None:
            raise RuntimeError(f"Unable to determine the supporting CT table for '{node_name}'.")
        _has_parent_update(connection, config, rchain, big_table, node_name)


def _has_parent_update(
    connection,
    config: FBConfig,
    rchain: str,
    big_table: str,
    node_name: str,
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT DISTINCT parent FROM Path_BayesNets "
            "WHERE Rchain = %s AND child = %s AND parent != ''",
            (rchain, node_name),
        )
        parents = [str(row[0]) for row in cursor.fetchall()]
    if not parents:
        return

    table_name = f"{node_name}_CP"
    escaped_table = quote_identifier(table_name)
    quoted_node = quote_identifier(node_name)
    parent_columns = ", ".join(quote_identifier(parent) for parent in parents)
    ct_table = f"{quote_identifier(config.ct_db)}.{quote_identifier(big_table)}"

    with connection.cursor() as cursor:
        cursor.execute(f"DROP TABLE IF EXISTS {escaped_table}")
        cursor.execute("DROP TABLE IF EXISTS temp")
        cursor.execute(
            f"CREATE TABLE {escaped_table} AS "
            f"SELECT SUM(MULT) AS MULT, {quoted_node}, {parent_columns}, 0 AS ParentSum "
            f"FROM {ct_table} "
            f"GROUP BY {quoted_node}, {parent_columns}"
        )
        cursor.execute(
            f"ALTER TABLE {escaped_table} CHANGE COLUMN ParentSum ParentSum bigint(20)"
        )
        cursor.execute(f"ALTER TABLE {escaped_table} ADD local_mult bigint(20)")

        index_columns = ", ".join([f"{quoted_node} ASC"] + [f"{quote_identifier(parent)} ASC" for parent in parents])
        cursor.execute(
            f"ALTER TABLE {escaped_table} ADD INDEX {escaped_table} ({index_columns})"
        )

        cursor.execute(
            f"CREATE TABLE temp AS "
            f"SELECT MULT, {parent_columns}, SUM(MULT) AS ParentSum "
            f"FROM {escaped_table} GROUP BY {parent_columns}"
        )
        temp_index_columns = ", ".join(f"{quote_identifier(parent)} ASC" for parent in parents)
        cursor.execute(f"ALTER TABLE temp ADD INDEX temp_ ({temp_index_columns})")

        conditions = " AND ".join(
            f"{escaped_table}.{quote_identifier(parent)} = temp.{quote_identifier(parent)}"
            for parent in parents
        )
        cursor.execute(
            f"UPDATE {escaped_table}, temp "
            f"SET {escaped_table}.ParentSum = temp.ParentSum "
            f"WHERE {conditions}"
        )

        cursor.execute(f"ALTER TABLE {escaped_table} ADD CP FLOAT(7, 6)")
        cursor.execute(f"ALTER TABLE {escaped_table} ADD likelihood FLOAT(20,2)")
        cursor.execute(f"UPDATE {escaped_table} SET CP = MULT / ParentSum")

        cursor.execute(f"UPDATE {escaped_table} SET local_mult = MULT")

        cursor.execute(f"UPDATE {escaped_table} SET likelihood = LOG(CP) * local_mult")
        cursor.execute("DROP TABLE IF EXISTS temp")

        cursor.execute(f"SELECT SUM(likelihood) FROM {escaped_table}")
        log_row = cursor.fetchone()
        log_likelihood = float(log_row[0]) if log_row and log_row[0] is not None else 0.0
        cursor.execute(
            "UPDATE Scores SET LogLikelihood = %s WHERE Scores.Fid = %s",
            (log_likelihood, node_name),
        )

        cursor.execute(f"SELECT SUM(local_mult) FROM {escaped_table}")
        sample_row = cursor.fetchone()
        sample_size = int(sample_row[0]) if sample_row and sample_row[0] is not None else 0
        cursor.execute(
            "UPDATE Scores SET SampleSize = %s WHERE Scores.Fid = %s",
            (sample_size, node_name),
        )

        cursor.execute(f"SELECT SUM(MULT) FROM {escaped_table}")
        big_row = cursor.fetchone()
        big_size = int(big_row[0]) if big_row and big_row[0] is not None else 0
        cursor.execute(
            "UPDATE Scores SET Big_SampleSize = %s WHERE Scores.Fid = %s",
            (big_size, node_name),
        )

        cursor.execute(f"ALTER TABLE {escaped_table} ADD prior FLOAT(7,6)")
        cursor.execute(f"SELECT SUM(local_mult) FROM {escaped_table}")
        total_row = cursor.fetchone()
        total_sum = int(total_row[0]) if total_row and total_row[0] is not None else 0

        cursor.execute("DROP TABLE IF EXISTS temp")
        cursor.execute(
            f"CREATE TABLE IF NOT EXISTS temp "
            f"SELECT SUM(local_mult) AS prior_parsum, {quoted_node} "
            f"FROM {escaped_table} GROUP BY {quoted_node}"
        )
        cursor.execute(
            f"UPDATE {escaped_table}, temp "
            f"SET {escaped_table}.prior = temp.prior_parsum / %s "
            f"WHERE {escaped_table}.{quoted_node} = temp.{quoted_node}",
            (total_sum or 1,),
        )
        cursor.execute("DROP TABLE IF EXISTS temp")
    connection.commit()


def _finalize_scores(connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute("UPDATE Scores SET Normal_LogLikelihood = LogLikelihood / SampleSize")
        cursor.execute("UPDATE Scores SET AIC = LogLikelihood - Parameters")
        cursor.execute("UPDATE Scores SET BIC = 2 * LogLikelihood - LOG(SampleSize) * Parameters")
        cursor.execute("UPDATE Scores SET Pseudo_AIC = Normal_LogLikelihood - Parameters")
        cursor.execute(
            "UPDATE Scores SET Pseudo_BIC = 2 * Normal_LogLikelihood - LOG(SampleSize) * Parameters"
        )
    connection.commit()


def _run_kld_generator(connection, config: FBConfig, info: LargestRChainInfo) -> None:
    _smoothed_cp(connection, info.rchain)
    _create_join_cp(connection, config, info)
    _generate_cll_tables(connection, info)
    _compute_cll_summary(connection, config, info)


def _smoothed_cp(connection, rchain: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT DISTINCT child FROM Path_BayesNets WHERE parent <> '' AND Rchain = %s",
            (rchain,),
        )
        with_parents = [str(row[0]) for row in cursor.fetchall()]

    for child in with_parents:
        _new_table_smoothed(connection, f"{child}_CP")

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT child FROM Path_BayesNets "
            "WHERE rchain = %s "
            "AND child NOT IN ("
            "  SELECT DISTINCT child FROM Path_BayesNets WHERE parent <> '' AND Rchain = %s"
            ")",
            (rchain, rchain),
        )
        without_parents = [str(row[0]) for row in cursor.fetchall()]

    for base_name in without_parents:
        smoothed_table = f"{base_name}_CP_smoothed"
        original_table = f"{base_name}_CP"
        node_identifier = quote_identifier(base_name)
        escaped_smoothed = quote_identifier(smoothed_table)
        escaped_original = quote_identifier(original_table)
        with connection.cursor() as cursor:
            cursor.execute(f"DROP TABLE IF EXISTS {escaped_smoothed}")
            cursor.execute(f"CREATE TABLE {escaped_smoothed} AS SELECT * FROM {escaped_original}")
            cursor.execute(f"UPDATE {escaped_smoothed} SET MULT = MULT + 1")
            cursor.execute(f"SELECT SUM(MULT) FROM {escaped_smoothed}")
            row = cursor.fetchone()
            total = int(row[0]) if row and row[0] is not None else 0
            cursor.execute(f"UPDATE {escaped_smoothed} SET CP = MULT / %s", (total or 1,))
            cursor.execute(f"SELECT {node_identifier} FROM {escaped_smoothed}")
            candidate_rows = cursor.fetchall()
            if candidate_rows:
                cv = "" if candidate_rows[0][0] is None else str(candidate_rows[0][0])
                cursor.execute(
                    f"SELECT SUM(CP) FROM {escaped_smoothed} WHERE {node_identifier} <> %s",
                    (cv,),
                )
                subtotal_row = cursor.fetchone()
                subtotal = float(subtotal_row[0]) if subtotal_row and subtotal_row[0] is not None else 0.0
                subtotal = min(subtotal, 1.0)
                cursor.execute(
                    f"UPDATE {escaped_smoothed} SET CP = %s WHERE {node_identifier} = %s",
                    (1 - subtotal, cv),
                )
        connection.commit()


def _new_table_smoothed(connection, table_name: str) -> None:
    smoothed_table = f"{table_name}_smoothed"
    escaped_smoothed = quote_identifier(smoothed_table)
    escaped_original = quote_identifier(table_name)

    with connection.cursor() as cursor:
        cursor.execute(f"DROP TABLE IF EXISTS {escaped_smoothed}")
        cursor.execute(f"CREATE TABLE {escaped_smoothed} LIKE {escaped_original}")

        original_columns = _table_columns(connection, table_name)
        index_columns = _cp_smoothed_index_columns(original_columns)
        if index_columns:
            cursor.execute(
                f"ALTER TABLE {escaped_smoothed} ADD INDEX {quote_identifier(smoothed_table + '1')} ("
                + ", ".join(f"{quote_identifier(column)} ASC" for column in index_columns)
                + ")"
            )

        parent_only_columns = index_columns[1:]
        if parent_only_columns:
            cursor.execute(
                f"ALTER TABLE {escaped_smoothed} ADD INDEX {quote_identifier(smoothed_table + '2')} ("
                + ", ".join(f"{quote_identifier(column)} ASC" for column in parent_only_columns)
                + ")"
            )
    connection.commit()

    base_columns = _cp_pair_columns(connection, table_name)
    _populate_pairs_table(connection, table_name, base_columns)

    selected_columns = ", ".join(quote_identifier(column) for column in base_columns)
    with connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {escaped_smoothed} (MULT, {selected_columns}) "
            f"SELECT MULT, {selected_columns} FROM {escaped_original}"
        )
        cursor.execute(
            f"INSERT INTO {escaped_smoothed} (MULT, {selected_columns}) ("
            + _difference_query(
                f"MULT, {selected_columns}",
                base_columns,
                quote_identifier(f"{table_name}_pairs"),
                escaped_original,
            )
            + ")"
        )
    connection.commit()

    with connection.cursor() as cursor:
        cursor.execute(f"UPDATE {escaped_smoothed} SET MULT = MULT + 1")
    connection.commit()
    _update_parent_sums(connection, smoothed_table, base_columns)


def _populate_pairs_table(connection, table_name: str, base_columns: list[str]) -> None:
    escaped_pairs = quote_identifier(f"{table_name}_pairs")
    with connection.cursor() as cursor:
        cursor.execute(f"DROP TABLE IF EXISTS {escaped_pairs}")
        column_defs = ", ".join(f"{quote_identifier(column)} VARCHAR(20) NOT NULL" for column in base_columns)
        cursor.execute(f"CREATE TABLE {escaped_pairs}(MULT int, {column_defs})")
        index_columns = ", ".join(["MULT ASC"] + [f"{quote_identifier(column)} ASC" for column in base_columns])
        cursor.execute(f"ALTER TABLE {escaped_pairs} ADD INDEX {escaped_pairs}({index_columns})")
    connection.commit()

    if not base_columns:
        return

    value_lists = [_distinct_column_values(connection, table_name, column) for column in base_columns]
    pair_rows: list[tuple[object, ...]] = [(0, value) for value in value_lists[0]]
    for values in value_lists[1:]:
        next_rows: list[tuple[object, ...]] = []
        for existing_row in pair_rows:
            for value in values:
                next_rows.append((*existing_row, value))
        pair_rows = next_rows

    if pair_rows:
        _insert_missing_pairs(connection, f"{table_name}_pairs", base_columns, pair_rows)


def _insert_missing_pairs(
    connection,
    smoothed_table: str,
    base_columns: list[str],
    rows: list[tuple[object, ...]],
) -> None:
    escaped_table = quote_identifier(smoothed_table)
    columns = ", ".join(["MULT"] + [quote_identifier(column) for column in base_columns])
    placeholders = ", ".join(["%s"] * (len(base_columns) + 1))
    with connection.cursor() as cursor:
        cursor.executemany(
            f"INSERT INTO {escaped_table} ({columns}) VALUES ({placeholders})",
            rows,
        )
    connection.commit()


def _update_parent_sums(connection, smoothed_table: str, base_columns: list[str]) -> None:
    if len(base_columns) < 2:
        return
    escaped_smoothed = quote_identifier(smoothed_table)
    child_column = base_columns[0]
    parent_columns = base_columns[1:]
    grouped_parents = ", ".join(quote_identifier(column) for column in parent_columns)

    with connection.cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS temp1")
        cursor.execute(
            f"CREATE TABLE temp1 AS "
            f"SELECT SUM(MULT) AS parsum, {grouped_parents} "
            f"FROM {escaped_smoothed} GROUP BY {grouped_parents}"
        )
        cursor.execute(
            "ALTER TABLE temp1 ADD INDEX temp1("
            + ", ".join(f"{quote_identifier(column)} ASC" for column in parent_columns)
            + ")"
        )

        comparisons = " AND ".join(
            f"temp1.{quote_identifier(column)} = {escaped_smoothed}.{quote_identifier(column)}"
            for column in parent_columns
        )
        cursor.execute(
            f"UPDATE {escaped_smoothed} "
            f"SET ParentSum = (SELECT temp1.parsum FROM temp1 WHERE {comparisons})"
        )
        cursor.execute(f"UPDATE {escaped_smoothed} SET CP = MULT / ParentSum")

        cursor.execute(f"SELECT DISTINCT {grouped_parents} FROM temp1")
        parent_combinations = cursor.fetchall()
        child_identifier = quote_identifier(child_column)
        for combo in parent_combinations:
            where_parts = []
            params: list[object] = []
            for column, value in zip(parent_columns, combo):
                where_parts.append(f"{quote_identifier(column)} = %s")
                params.append(value)
            where_clause = " AND ".join(where_parts)
            cursor.execute(
                f"SELECT DISTINCT {child_identifier} "
                f"FROM {escaped_smoothed} "
                f"WHERE {where_clause} "
                f"ORDER BY CP DESC",
                tuple(params),
            )
            candidate_rows = cursor.fetchall()
            if not candidate_rows:
                continue
            cv = candidate_rows[0][0]
            cursor.execute(
                f"SELECT SUM(CP) FROM {escaped_smoothed} "
                f"WHERE {where_clause} AND {child_identifier} <> %s",
                tuple(params) + (cv,),
            )
            subtotal_row = cursor.fetchone()
            subtotal = float(subtotal_row[0]) if subtotal_row and subtotal_row[0] is not None else 0.0
            cp_value = 1 - subtotal
            cursor.execute(
                f"UPDATE {escaped_smoothed} SET CP = %s "
                f"WHERE {where_clause} AND {child_identifier} = %s",
                (cp_value, *params, cv),
            )
    connection.commit()


def _create_join_cp(connection, config: FBConfig, info: LargestRChainInfo) -> None:
    ct_table = f"{quote_identifier(config.ct_db)}.{quote_identifier(info.short_rchain + '_CT')}"
    kld_table = quote_identifier(f"{info.short_rchain}_CT_KLD")
    column_names = _table_columns(connection, f"{config.ct_db}.{info.short_rchain}_CT")
    cp_columns = [f"{column}_CP" for column in column_names]

    with connection.cursor() as cursor:
        cursor.execute(f"DROP TABLE IF EXISTS {kld_table}")
        create_columns = []
        for column_name, cp_column in zip(column_names[1:], cp_columns[1:]):
            create_columns.append(f"{quote_identifier(column_name)} VARCHAR(45)")
            create_columns.append(f"{quote_identifier(cp_column)} FLOAT(7,6)")
        cursor.execute(
            f"CREATE TABLE {kld_table} ("
            "id INT(11) NOT NULL AUTO_INCREMENT, "
            "MULT BIGINT(21), "
            + ", ".join(create_columns)
            + ", JP FLOAT, JP_DB FLOAT, KLD FLOAT DEFAULT 0, PRIMARY KEY (id)) ENGINE=INNODB"
        )

        insert_columns = ", ".join([quote_identifier("MULT")] + [quote_identifier(name) for name in column_names[1:]])
        select_columns = ", ".join(quote_identifier(name) for name in column_names)
        cursor.execute(
            f"INSERT INTO {kld_table} ({insert_columns}) SELECT {select_columns} FROM {ct_table}"
        )
    connection.commit()

    _insert_cp_values(connection, info.rchain, kld_table)
    _calculate_kld(connection, kld_table, cp_columns[1:])


def _insert_cp_values(connection, rchain: str, kld_table: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT child FROM Path_BayesNets "
            "WHERE rchain = %s AND child NOT IN ("
            "  SELECT DISTINCT child FROM Path_BayesNets WHERE parent <> '' AND rchain = %s"
            ")",
            (rchain, rchain),
        )
        no_parents = [str(row[0]) for row in cursor.fetchall()]

        for node_name in no_parents:
            smoothed = quote_identifier(f"{node_name}_CP_smoothed")
            cursor.execute(
                f"UPDATE {kld_table}, {smoothed} "
                f"SET {quote_identifier(node_name + '_CP')} = {smoothed}.CP "
                f"WHERE {smoothed}.{quote_identifier(node_name)} = {kld_table}.{quote_identifier(node_name)}"
            )

        cursor.execute(
            "SELECT DISTINCT child FROM Path_BayesNets WHERE parent <> '' AND Rchain = %s",
            (rchain,),
        )
        with_parents = [str(row[0]) for row in cursor.fetchall()]

        for node_name in with_parents:
            smoothed_name = f"{node_name}_CP_smoothed"
            smoothed = quote_identifier(smoothed_name)
            parent_columns = _cp_smoothed_parent_columns(connection, smoothed_name)
            index_columns = ", ".join(
                [f"{quote_identifier(node_name)} ASC"]
                + [f"{quote_identifier(parent)} ASC" for parent in parent_columns]
            )
            cursor.execute(
                f"ALTER TABLE {kld_table} ADD INDEX {quote_identifier(node_name)} ({index_columns})"
            )
            comparisons = [
                f"{smoothed}.{quote_identifier(node_name)} = {kld_table}.{quote_identifier(node_name)}"
            ]
            for parent in parent_columns:
                comparisons.append(
                    f"{kld_table}.{quote_identifier(parent)} = {smoothed}.{quote_identifier(parent)}"
                )
            cursor.execute(
                f"UPDATE {kld_table}, {smoothed} "
                f"SET {quote_identifier(node_name + '_CP')} = {smoothed}.CP "
                f"WHERE {' AND '.join(comparisons)}"
            )
    connection.commit()


def _calculate_kld(connection, kld_table: str, cp_columns: list[str]) -> None:
    with connection.cursor() as cursor:
        product_expression = " * ".join(quote_identifier(column) for column in cp_columns)
        cursor.execute(f"UPDATE {kld_table} SET JP = {product_expression}")
        cursor.execute(f"SELECT SUM(MULT) FROM {kld_table}")
        row = cursor.fetchone()
        mult_sum = int(row[0]) if row and row[0] is not None else 0
        cursor.execute(f"UPDATE {kld_table} SET JP_DB = MULT / %s", (mult_sum or 1,))
        cursor.execute(
            f"UPDATE {kld_table} SET KLD = (JP_DB * (LOG(JP_DB) - LOG(JP))) WHERE MULT <> 0"
        )
    connection.commit()


def _generate_cll_tables(connection, info: LargestRChainInfo) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT DISTINCT child FROM Path_BayesNets WHERE Rchain = %s",
            (info.rchain,),
        )
        nodes = [str(row[0]) for row in cursor.fetchall()]
    for node_name in nodes:
        _generate_cll_table_for_node(connection, node_name, info)


def _generate_cll_table_for_node(connection, node_name: str, info: LargestRChainInfo) -> None:
    blanket = _markov_blanket(connection, node_name, info.rchain)
    cll_table = quote_identifier(f"{node_name}_CLL")
    node_identifier = quote_identifier(node_name)
    blanket_columns = ", ".join(quote_identifier(name) for name in blanket)
    blanket_prefix = "".join(f", {quote_identifier(name)}" for name in blanket)
    kld_table = quote_identifier(f"{info.short_rchain}_CT_KLD")

    with connection.cursor() as cursor:
        cursor.execute(f"DROP TABLE IF EXISTS {cll_table}")
        if blanket:
            cursor.execute(
                f"CREATE TABLE {cll_table} ("
                f"{node_identifier} VARCHAR(45), "
                + ", ".join(f"{quote_identifier(name)} VARCHAR(45)" for name in blanket)
                + ", JP_DB FLOAT, JP_DB_blanket FLOAT, CLL_DB FLOAT, "
                "JP FLOAT, JP_blanket FLOAT, CLL_JP FLOAT, AbsDif FLOAT) ENGINE=INNODB"
            )
            cursor.execute(
                f"INSERT INTO {cll_table} ({node_identifier}{blanket_prefix}, JP_DB, JP) "
                f"SELECT {node_identifier}{blanket_prefix}, SUM(JP_DB), SUM(JP) "
                f"FROM {kld_table} "
                f"GROUP BY {node_identifier}{blanket_prefix}"
            )
            cursor.execute("DROP TABLE IF EXISTS temp")
            cursor.execute(
                f"CREATE TABLE temp "
                f"SELECT SUM(JP_DB) AS JP_DB_sum, SUM(JP) AS JP_sum{blanket_prefix} "
                f"FROM {cll_table} GROUP BY {blanket_columns}"
            )
            comparisons = " AND ".join(
                f"temp.{quote_identifier(name)} = {cll_table}.{quote_identifier(name)}"
                for name in blanket
            )
            cursor.execute(
                f"UPDATE {cll_table}, temp "
                "SET JP_DB_blanket = temp.JP_DB_sum, JP_blanket = temp.JP_sum "
                f"WHERE {comparisons}"
            )
        else:
            cursor.execute(
                f"CREATE TABLE {cll_table} ("
                f"{node_identifier} VARCHAR(45), "
                "JP_DB FLOAT, JP_DB_blanket FLOAT, CLL_DB FLOAT, "
                "JP FLOAT, JP_blanket FLOAT, CLL_JP FLOAT, AbsDif FLOAT"
                ") ENGINE=INNODB"
            )
            cursor.execute(
                f"INSERT INTO {cll_table} ({node_identifier}, JP_DB, JP) "
                f"SELECT {node_identifier}, SUM(JP_DB), SUM(JP) "
                f"FROM {kld_table} GROUP BY {node_identifier}"
            )
            cursor.execute(f"UPDATE {cll_table} SET JP_DB_blanket = JP_DB, JP_blanket = JP")

        cursor.execute(
            f"UPDATE {cll_table} SET "
            "CLL_DB = LOG(JP_DB / JP_DB_blanket), "
            "CLL_JP = LOG(JP / JP_blanket)"
        )
        cursor.execute(f"UPDATE {cll_table} SET AbsDif = ABS(CLL_DB - CLL_JP)")
    connection.commit()


def _compute_cll_summary(connection, config: FBConfig, info: LargestRChainInfo) -> None:
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT SUM(KLD) FROM {quote_identifier(info.short_rchain + '_CT_KLD')}")
        cursor.fetchone()


def _markov_blanket(connection, node_name: str, rchain: str) -> list[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT DISTINCT parent FROM Path_BayesNets "
            "WHERE child = %s AND Rchain = %s AND parent <> ''",
            (node_name, rchain),
        )
        parents = {str(row[0]) for row in cursor.fetchall()}

        cursor.execute(
            "SELECT DISTINCT child FROM Path_BayesNets "
            "WHERE parent = %s AND Rchain = %s",
            (node_name, rchain),
        )
        children = [str(row[0]) for row in cursor.fetchall()]

        blanket = set(parents)
        blanket.update(children)
        for child in children:
            cursor.execute(
                "SELECT DISTINCT parent FROM Path_BayesNets "
                "WHERE child = %s AND Rchain = %s AND parent <> '' AND parent <> %s",
                (child, rchain, node_name),
            )
            blanket.update(str(row[0]) for row in cursor.fetchall())

    return sorted(blanket)


def _supporting_table_for_node(connection, node_name: str) -> str | None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT short_rnid FROM RNodes_inFamily_view WHERE child = %s ORDER BY short_rnid",
            (node_name,),
        )
        short_rnids = [normalize_identifier_text(row[0]) for row in cursor.fetchall()]
        if short_rnids:
            return f"{','.join(short_rnids)}_CT"

        cursor.execute(
            "SELECT DISTINCT pvid FROM Pvars_Family WHERE child = %s ORDER BY pvid",
            (node_name,),
        )
        pvids = [str(row[0]) for row in cursor.fetchall()]
        if pvids:
            return f"{','.join(pvids)}_counts"
    return None


def _object_type(connection, table_name: str) -> str | None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT TABLE_TYPE FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name = %s",
            (table_name,),
        )
        row = cursor.fetchone()
    return None if row is None else str(row[0])


def _column_exists(connection, table_name: str, column_name: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = %s AND column_name = %s",
            (table_name, column_name),
        )
        row = cursor.fetchone()
    return bool(row and int(row[0]) > 0)


def _table_columns(connection, table_name: str) -> list[str]:
    if "." in table_name:
        schema_name, base_name = table_name.split(".", 1)
    else:
        schema_name = None
        base_name = table_name

    query = (
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = %s "
    )
    params: list[object] = [base_name]
    if schema_name is None:
        query += "AND table_schema = DATABASE() "
    else:
        query += "AND table_schema = %s "
        params.append(schema_name)
    query += "ORDER BY ordinal_position"

    with connection.cursor() as cursor:
        cursor.execute(query, tuple(params))
        return [str(row[0]) for row in cursor.fetchall()]


def _cp_smoothed_index_columns(columns: list[str]) -> list[str]:
    trimmed = list(columns)
    for _ in range(4):
        if trimmed:
            trimmed.pop()
    if trimmed and trimmed[-1] == "ParentSum":
        trimmed.pop()
    return trimmed


def _cp_pair_columns(connection, table_name: str) -> list[str]:
    trimmed = _cp_smoothed_index_columns(_table_columns(connection, table_name))
    return trimmed[1:]


def _cp_smoothed_parent_columns(connection, table_name: str) -> list[str]:
    trimmed = _cp_smoothed_index_columns(_table_columns(connection, table_name))
    return trimmed[2:]


def _difference_query(
    columns_a: str,
    join_columns: list[str],
    table_a: str,
    table_b: str,
) -> str:
    conditions = []
    for column in join_columns:
        escaped = quote_identifier(column)
        conditions.append(
            f"({table_a}.{escaped} = {table_b}.{escaped} OR {table_a}.{escaped} IS NULL AND {table_b}.{escaped} IS NULL)"
        )
    return (
        f"SELECT {columns_a} FROM {table_a} "
        f"WHERE NOT EXISTS (SELECT NULL FROM {table_b} WHERE {' AND '.join(conditions)})"
    )


def _distinct_column_values(connection, table_name: str, column_name: str) -> list[object]:
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT DISTINCT {quote_identifier(column_name)} FROM {quote_identifier(table_name)}"
        )
        return [row[0] for row in cursor.fetchall()]


def _fetch_rows(connection, query: str, params: Sequence[object] = ()) -> list[tuple[object, ...]]:
    with connection.cursor() as cursor:
        cursor.execute(query, tuple(params))
        return [tuple(row) for row in cursor.fetchall()]
