from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from .config import FBConfig

if TYPE_CHECKING:
    from mysql.connector.connection import MySQLConnection
else:
    MySQLConnection = Any

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
        cursor.execute(f"USE `{database_name}`;")
    connection.commit()


def call_procedure(connection: MySQLConnection, procedure_name: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(f"CALL {procedure_name}();")
    connection.commit()
