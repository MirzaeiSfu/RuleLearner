from __future__ import annotations

from dataclasses import dataclass, field

from .config import FBConfig
from .db import call_procedure, connect, normalize_identifier_text, quote_identifier, use_database


MAX_NUM_OF_PVARS = 5


@dataclass(frozen=True)
class RChainInfo:
    id: str
    short_id: str


@dataclass
class RelationshipLattice:
    rchain_infos_per_level: dict[int, list[RChainInfo]] = field(default_factory=dict)
    lattice_height: int = 0
    longest_rchain: str = ""

    def add_rchain_info(self, rchain_info: RChainInfo, level: int) -> None:
        if level > self.lattice_height:
            self.lattice_height = level
            self.longest_rchain = rchain_info.id
        self.rchain_infos_per_level.setdefault(level, []).append(rchain_info)

    def get_rchains_info(self, length: int) -> list[RChainInfo]:
        return self.rchain_infos_per_level.get(length, [])


def build_global_lattice(config: FBConfig) -> RelationshipLattice:
    connection = connect(config, database=config.setup_db)
    try:
        use_database(connection, config.setup_db)
        _reset_global_lattice_tables(connection)
        rnode_ids = _fetch_rnode_ids(connection)
        _initialize_global_lattice(connection, rnode_ids)
        _generate_global_lattice_tree(connection, rnode_ids)
        _populate_global_lattice_mapping(connection)
        return _load_relationship_lattice(connection)
    finally:
        connection.close()


def build_ct(config: FBConfig) -> RelationshipLattice:
    if config.counting_strategy != 0:
        raise NotImplementedError(
            "The Python CT builder currently supports only CountingStrategy=0 (PreCount)."
        )

    connection = connect(config, database=config.bn_db)
    try:
        _recreate_ct_schema(connection, config)

        use_database(connection, config.bn_db)
        call_procedure(connection, "cascadeFS")
        call_procedure(connection, "populateLattice")
        lattice = _load_relationship_lattice(connection)

        if config.continuous:
            raise NotImplementedError("Continuous=1 is not supported in the Python CT builder yet.")

        call_procedure(connection, "populateMQ")
        call_procedure(connection, "populateMQRChain")

        _build_rchain_counts(connection, config, lattice)
        _build_pvars_counts(connection, config)

        if config.link_correlations and lattice.lattice_height != 0:
            join_table_queries = _create_join_table_queries(connection)
            for rchain_info in lattice.get_rchains_info(1):
                _build_rnode_ct(connection, config, rchain_info, join_table_queries)
            for length in range(2, lattice.lattice_height + 1):
                _build_rchain_cts(
                    connection,
                    config,
                    lattice.get_rchains_info(length),
                    length,
                    join_table_queries,
                )

        return lattice
    finally:
        connection.close()


def _reset_global_lattice_tables(connection) -> None:
    with connection.cursor() as cursor:
        for table_name in ("lattice_mapping", "lattice_membership", "lattice_rel", "lattice_set"):
            cursor.execute(f"DELETE FROM {quote_identifier(table_name)}")
    connection.commit()


def _fetch_rnode_ids(connection) -> list[str]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT rnid FROM RNodes ORDER BY rnid")
        return [str(row[0]) for row in cursor.fetchall()]


def _initialize_global_lattice(connection, rnode_ids: list[str]) -> None:
    with connection.cursor() as cursor:
        for rnode_id in rnode_ids:
            cursor.execute(
                "INSERT INTO lattice_set (name, length) VALUES (%s, 1)",
                (rnode_id,),
            )
            cursor.execute(
                "INSERT INTO lattice_rel (parent, child, removed) VALUES ('EmptySet', %s, %s)",
                (rnode_id, rnode_id),
            )
            cursor.execute(
                "INSERT INTO lattice_membership (name, member) VALUES (%s, %s)",
                (rnode_id, rnode_id),
            )
    connection.commit()


def _generate_global_lattice_tree(connection, rnode_ids: list[str]) -> None:
    for set_length in range(1, len(rnode_ids)):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT name FROM lattice_set WHERE length = %s ORDER BY name",
                (set_length,),
            )
            existing_sets = [str(row[0]) for row in cursor.fetchall()]

        for first_set in rnode_ids:
            for second_set in existing_sets:
                second_parts = _node_split(second_set)
                if not _check_constraints(connection, first_set, second_parts):
                    continue

                new_set = sorted(set([first_set, *second_parts]))
                new_set_name = ",".join(new_set)
                if new_set_name == second_set:
                    continue

                with connection.cursor() as cursor:
                    cursor.execute(
                        "INSERT IGNORE INTO lattice_set (name, length) VALUES (%s, %s)",
                        (new_set_name, len(new_set)),
                    )
                    cursor.execute(
                        "INSERT IGNORE INTO lattice_rel (parent, child, removed) VALUES (%s, %s, %s)",
                        (second_set, new_set_name, first_set),
                    )
                    cursor.execute(
                        "INSERT IGNORE INTO lattice_membership (name, member) VALUES (%s, %s)",
                        (new_set_name, first_set),
                    )
                    for member in new_set:
                        cursor.execute(
                            "INSERT IGNORE INTO lattice_membership (name, member) VALUES (%s, %s)",
                            (new_set_name, member),
                        )
                connection.commit()


def _check_constraints(connection, first_set: str, second_set_parts: list[str]) -> bool:
    first_keys = _fetch_rnode_pvars(connection, first_set)
    second_keys: set[str] = set()
    for second_set in second_set_parts:
        second_keys.update(_fetch_rnode_pvars(connection, second_set))

    if len(first_keys | second_keys) > MAX_NUM_OF_PVARS:
        return False
    return bool(first_keys & second_keys)


def _fetch_rnode_pvars(connection, rnode_id: str) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pvid1, pvid2 FROM RNodes WHERE rnid = %s",
            (rnode_id,),
        )
        keys: set[str] = set()
        for row in cursor.fetchall():
            if row[0]:
                keys.add(str(row[0]))
            if row[1]:
                keys.add(str(row[1]))
        return keys


def _populate_global_lattice_mapping(connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT name FROM lattice_set ORDER BY length, name")
        rnodes = [str(row[0]) for row in cursor.fetchall()]

        for rnid in rnodes:
            short_ids: list[str] = []
            for rnode in _node_split(rnid):
                cursor.execute(
                    "SELECT short_rnid FROM LatticeRNodes WHERE orig_rnid = %s",
                    (rnode,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise RuntimeError(f"No short_rnid found for setup RNode '{rnode}'.")
                short_ids.append(normalize_identifier_text(row[0]))
            cursor.execute(
                "INSERT INTO lattice_mapping (orig_rnid, short_rnid) VALUES (%s, %s)",
                (rnid, ",".join(short_ids)),
            )
    connection.commit()


def _load_relationship_lattice(connection) -> RelationshipLattice:
    lattice = RelationshipLattice()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT lattice_set.name, lattice_mapping.short_rnid, lattice_set.length "
            "FROM lattice_set "
            "JOIN lattice_mapping ON lattice_set.name = lattice_mapping.orig_rnid "
            "ORDER BY lattice_set.length, lattice_set.name"
        )
        for name, short_rnid, length in cursor.fetchall():
            lattice.add_rchain_info(
                RChainInfo(id=str(name), short_id=normalize_identifier_text(short_rnid)),
                int(length),
            )
    return lattice


def _recreate_ct_schema(connection, config: FBConfig) -> None:
    with connection.cursor() as cursor:
        cursor.execute(f"DROP SCHEMA IF EXISTS {quote_identifier(config.ct_db)}")
        cursor.execute(
            f"CREATE SCHEMA {quote_identifier(config.ct_db)} COLLATE {config.dbcollation}"
        )
    connection.commit()


def _build_rchain_counts(connection, config: FBConfig, lattice: RelationshipLattice) -> None:
    copy_to_ct = not config.link_correlations
    for length in range(1, lattice.lattice_height + 1):
        for rchain_info in lattice.get_rchains_info(length):
            use_database(connection, config.bn_db)
            counts_query = _generate_counts_table_query(connection, rchain_info.id, rchain_info.short_id)
            _create_table_from_query(
                connection,
                config.ct_db,
                f"{rchain_info.short_id}_counts",
                counts_query,
                storage_engine="InnoDB",
            )
            if copy_to_ct:
                _create_table_from_query(
                    connection,
                    config.ct_db,
                    f"{rchain_info.short_id}_CT",
                    counts_query,
                    storage_engine=None,
                )


def _build_pvars_counts(connection, config: FBConfig) -> None:
    use_database(connection, config.bn_db)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pvid FROM PVariables ORDER BY pvid")
        pvids = [str(row[0]) for row in cursor.fetchall()]

    for pvid in pvids:
        use_database(connection, config.bn_db)
        select_aliases = _metaquery_entries(connection, pvid, "Counts", "SELECT")
        select_string = ", ".join(select_aliases)
        from_string = ", ".join(_metaquery_entries(connection, pvid, "Counts", "FROM"))
        groupby_string = ", ".join(_metaquery_entries(connection, pvid, "Counts", "GROUPBY"))
        where_entries = _metaquery_entries(connection, pvid, "Counts", "WHERE")

        query = f"SELECT {select_string} FROM {from_string}"
        if where_entries:
            query += " WHERE " + " AND ".join(where_entries)
        if groupby_string:
            query += " GROUP BY " + groupby_string
        query += " HAVING MULT > 0"

        _create_table_from_query(
            connection,
            config.ct_db,
            f"{pvid}_counts",
            query,
            storage_engine="InnoDB",
        )


def _generate_counts_table_query(connection, rchain: str, short_rchain: str) -> str:
    select_aliases = _metaquery_entries(connection, rchain, "Counts", "SELECT")
    from_aliases = _metaquery_entries(connection, rchain, "Counts", "FROM")
    where_entries = _metaquery_entries(connection, rchain, "Counts", "WHERE")
    groupby_entries = _metaquery_entries(connection, rchain, "Counts", "GROUPBY")

    query = f"SELECT {', '.join(select_aliases)} FROM {', '.join(from_aliases)}"
    if where_entries:
        query += " WHERE " + " AND ".join(where_entries)
    if groupby_entries:
        query += " GROUP BY " + ", ".join(groupby_entries)
    return query


def _build_rnode_ct(
    connection,
    config: FBConfig,
    rchain_info: RChainInfo,
    join_table_queries: dict[str, str],
) -> None:
    use_database(connection, config.bn_db)
    counts_subquery = _generate_counts_table_query(connection, rchain_info.id, rchain_info.short_id)
    _build_rnode_flat(connection, config, rchain_info.id, rchain_info.short_id, counts_subquery)
    star_subquery = _build_rnode_star_query(connection, config, rchain_info.id)
    false_subquery = _build_false_subquery(connection, config.ct_db, star_subquery, f"{rchain_info.short_id}_flat")
    ct_query = _build_rnode_ct_creation_query(
        connection,
        rchain_info.id,
        rchain_info.short_id,
        counts_subquery,
        false_subquery,
        join_table_queries,
    )
    _create_table_from_query(
        connection,
        config.ct_db,
        f"{rchain_info.short_id}_CT",
        ct_query,
        storage_engine="InnoDB",
    )


def _build_rchain_cts(
    connection,
    config: FBConfig,
    rchain_infos: list[RChainInfo],
    length: int,
    join_table_queries: dict[str, str],
) -> str | None:
    final_table_name: str | None = None
    flat_counter = 0
    use_database(connection, config.bn_db)

    for rchain_info in rchain_infos:
        current_ct_table = f"{rchain_info.short_id}_counts"
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT lattice_rel.removed, lattice_mapping.short_rnid "
                "FROM lattice_rel "
                "JOIN lattice_mapping ON lattice_rel.removed = lattice_mapping.orig_rnid "
                "WHERE child = %s "
                "ORDER BY removed ASC",
                (rchain_info.id,),
            )
            removed_rows = [
                (str(row[0]), normalize_identifier_text(row[1]))
                for row in cursor.fetchall()
            ]

        for index, (removed, removed_short) in enumerate(removed_rows):
            use_database(connection, config.bn_db)
            base_name = f"{rchain_info.short_id}_{removed_short}"
            select_string = ", ".join(
                _metaquery_entries(connection, rchain_info.id, "Star", "SELECT", entry_type=removed)
            )
            from_entries = _metaquery_entries(connection, rchain_info.id, "Star", "FROM", entry_type=removed)
            mult_string = " * ".join(f"{entry}.MULT" for entry in from_entries)
            from_string = ", ".join(from_entries)
            where_entries = _metaquery_entries(connection, rchain_info.id, "Star", "WHERE", entry_type=removed)

            star_query = f"SELECT {mult_string} AS `MULT`"
            if select_string:
                star_query += f", {select_string}"
            star_query += f" FROM {from_string}"
            if where_entries:
                star_query += " WHERE " + " AND ".join(where_entries)

            current_flat_table = f"{removed_short}{length}_{flat_counter}_flat"
            flat_query = f"SELECT SUM(`{current_ct_table}`.MULT) AS `MULT`"
            if select_string:
                flat_query += f", {select_string} FROM `{current_ct_table}` GROUP BY {select_string}"
            else:
                flat_query += f" FROM `{current_ct_table}`"
            _create_table_from_query(
                connection,
                config.ct_db,
                current_flat_table,
                flat_query,
                storage_engine="InnoDB",
            )
            _add_covering_index(connection, config.ct_db, current_flat_table)

            false_query = _build_false_subquery(connection, config.ct_db, star_query, current_flat_table)

            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = %s AND table_name = %s "
                    "ORDER BY ordinal_position",
                    (config.ct_db, current_ct_table),
                )
                columns = [str(row[0]) for row in cursor.fetchall()]
            ct_join_string = ", ".join(quote_identifier(column) for column in columns)

            next_ct_table = (
                f"{base_name}_CT"
                if index < len(removed_rows) - 1
                else f"{rchain_info.short_id}_CT"
            )
            ct_query = (
                f"SELECT {ct_join_string} FROM `{current_ct_table}` WHERE MULT > 0 "
                "UNION ALL "
                f"SELECT {ct_join_string} "
                f"FROM ({false_query}) AS FALSE_TABLE, ({join_table_queries[removed_short]}) AS JOIN_TABLE "
                "WHERE MULT > 0"
            )
            _create_table_from_query(
                connection,
                config.ct_db,
                next_ct_table,
                ct_query,
                storage_engine="InnoDB",
            )
            current_ct_table = next_ct_table
            final_table_name = current_ct_table
            flat_counter += 1

    return final_table_name


def _build_rnode_star_query(connection, config: FBConfig, rnode: str) -> str:
    use_database(connection, config.bn_db)
    select_entries = _metaquery_entries(connection, rnode, "Star", "SELECT", distinct=True)
    from_entries = _metaquery_entries(connection, rnode, "Star", "FROM")
    multiplication = " * ".join(f"{entry}.MULT" for entry in from_entries)
    from_string = f"{quote_identifier(config.ct_db)}." + f", {quote_identifier(config.ct_db)}.".join(from_entries)
    query = f"SELECT {multiplication} AS MULT"
    if select_entries:
        query += ", " + ", ".join(select_entries)
    query += f" FROM {from_string}"
    return query


def _build_rnode_flat(connection, config: FBConfig, rnode: str, short_rnode: str, counts_subquery: str) -> None:
    use_database(connection, config.bn_db)
    select_entries = _metaquery_entries(connection, rnode, "Flat", "SELECT")
    from_entries = _metaquery_entries(connection, rnode, "Flat", "FROM")
    from_string = ", ".join(from_entries).replace(
        f"`{short_rnode}_counts`",
        f"({counts_subquery}) AS {short_rnode}_counts",
    )
    query = f"SELECT {', '.join(select_entries)} FROM {from_string}"
    groupby_entries = _metaquery_entries(connection, rnode, "Flat", "GROUPBY")
    if groupby_entries:
        query += " GROUP BY " + ", ".join(groupby_entries)
    _create_table_from_query(
        connection,
        config.ct_db,
        f"{short_rnode}_flat",
        query,
        storage_engine="InnoDB",
    )
    _add_covering_index(connection, config.ct_db, f"{short_rnode}_flat")


def _build_rnode_ct_creation_query(
    connection,
    rnode: str,
    short_rnode: str,
    counts_subquery: str,
    false_subquery: str,
    join_table_queries: dict[str, str],
) -> str:
    columns = _metaquery_entries(connection, rnode, "Counts", "SELECT")
    if columns:
        union_columns = "MULT, " + ", ".join(_alias_names(columns))
    else:
        union_columns = "MULT"
    return (
        f"SELECT {union_columns} "
        f"FROM ({counts_subquery}) AS {short_rnode}_counts "
        "WHERE MULT > 0 "
        "UNION ALL "
        f"SELECT {union_columns} "
        f"FROM ({false_subquery}) AS FALSE_TABLE, ({join_table_queries[short_rnode]}) AS JOIN_TABLE "
        "WHERE MULT > 0"
    )


def _create_join_table_queries(connection) -> dict[str, str]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT orig_rnid, short_rnid FROM LatticeRNodes")
        rows = [(str(row[0]), normalize_identifier_text(row[1])) for row in cursor.fetchall()]

    queries: dict[str, str] = {}
    for orig_rnid, short_rnid in rows:
        columns = _metaquery_entries(connection, orig_rnid, "Join", "COLUMN")
        query = f'SELECT "F" AS {quote_identifier(orig_rnid)}'
        if columns:
            query += ", " + ", ".join(columns)
        queries[short_rnid] = query
    return queries


def _build_false_subquery(connection, ct_db: str, left_subquery: str, table_name: str) -> str:
    join_columns = _table_columns(connection, ct_db, table_name)[1:]
    comparisons = " AND ".join(
        f"SUBQUERY.{quote_identifier(column)} = {quote_identifier(table_name)}.{quote_identifier(column)}"
        for column in join_columns
    )
    selected = ", ".join(
        [f"SUBQUERY.{quote_identifier(column)}" for column in join_columns]
    )
    if selected:
        selected = ", " + selected
    return (
        "SELECT "
        f"SUBQUERY.MULT - IFNULL({quote_identifier(table_name)}.MULT, 0) AS MULT"
        f"{selected} "
        f"FROM ({left_subquery}) AS SUBQUERY "
        f"LEFT JOIN {quote_identifier(table_name)} "
        f"ON {comparisons}"
    )


def _create_table_from_query(
    connection,
    schema_name: str,
    table_name: str,
    select_query: str,
    storage_engine: str | None,
) -> None:
    use_database(connection, schema_name)
    create_query = f"CREATE TABLE {quote_identifier(table_name)}"
    if storage_engine is not None:
        create_query += f" ENGINE = {storage_engine}"
    create_query += f" AS {select_query}"
    with connection.cursor() as cursor:
        cursor.execute(f"DROP TABLE IF EXISTS {quote_identifier(table_name)}")
        cursor.execute(create_query)
    connection.commit()


def _metaquery_entries(
    connection,
    lattice_point: str,
    table_type: str,
    clause_type: str,
    entry_type: str | None = None,
    distinct: bool = False,
) -> list[str]:
    select_clause = "SELECT DISTINCT Entries" if distinct else "SELECT Entries"
    query = (
        f"{select_clause} FROM MetaQueries "
        "WHERE Lattice_Point = %s "
        "AND TableType = %s "
        "AND ClauseType = %s"
    )
    params: list[str] = [lattice_point, table_type, clause_type]
    if entry_type is not None:
        query += " AND EntryType = %s"
        params.append(entry_type)
    with connection.cursor() as cursor:
        cursor.execute(query, tuple(params))
        return [str(row[0]) for row in cursor.fetchall()]


def _alias_names(select_aliases: list[str]) -> list[str]:
    aliases: list[str] = []
    for select_alias in select_aliases:
        _, alias = select_alias.split(" AS ", 1)
        aliases.append(alias)
    return aliases


def _add_covering_index(connection, schema_name: str, table_name: str) -> None:
    columns = _table_columns(connection, schema_name, table_name)[1:]
    if not columns:
        return
    use_database(connection, schema_name)
    with connection.cursor() as cursor:
        cursor.execute(
            f"ALTER TABLE {quote_identifier(table_name)} "
            "ADD INDEX CoveringIndex ("
            + ", ".join(quote_identifier(column) for column in columns)
            + ")"
        )
    connection.commit()


def _table_columns(connection, schema_name: str, table_name: str) -> list[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s "
            "ORDER BY ordinal_position",
            (schema_name, table_name),
        )
        return [str(row[0]) for row in cursor.fetchall()]


def _node_split(node: str) -> list[str]:
    return node.replace("),", ") ").split(" ")
