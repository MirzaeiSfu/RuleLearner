from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from .config import FBConfig

if TYPE_CHECKING:
    from mysql.connector.connection import MySQLConnection
else:
    MySQLConnection = Any


class SourceSchemaError(RuntimeError):
    """Raised when the source schema is incompatible with FactorBase expectations."""


def quote_identifier(identifier: str) -> str:
    return "`" + identifier.replace("`", "``") + "`"


def connect(config: FBConfig, database: Optional[str] = None) -> MySQLConnection:
    import mysql.connector

    return mysql.connector.connect(
        host=config.host,
        port=config.port,
        user=config.dbusername,
        password=config.dbpassword,
        database=database,
        autocommit=False,
    )


def use_database(connection: MySQLConnection, database_name: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(f"USE {quote_identifier(database_name)};")
    connection.commit()


def call_procedure(connection: MySQLConnection, procedure_name: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(f"CALL {procedure_name}();")
    connection.commit()


def foreign_key_constraint_count(connection: MySQLConnection, schema_name: str) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.TABLE_CONSTRAINTS
            WHERE TABLE_SCHEMA = %s
              AND CONSTRAINT_TYPE = 'FOREIGN KEY'
            """,
            (schema_name,),
        )
        row = cursor.fetchone()
    return int(row[0]) if row is not None else 0


def ensure_schema_has_foreign_keys(connection: MySQLConnection, schema_name: str) -> None:
    fk_count = foreign_key_constraint_count(connection, schema_name)
    if fk_count > 0:
        return

    raise SourceSchemaError(
        (
            f"Source database '{schema_name}' has no FOREIGN KEY constraints. "
            "FactorBase BN propagation may fail with empty lattice / empty result set. "
            "Re-import the dataset with FK constraints (for grid-style data, "
            "edges.source_node_id -> nodes.node_id and "
            "edges.target_node_id -> nodes.node_id), then rerun."
        )
    )
