from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mysql.connector.connection import MySQLConnection
else:
    MySQLConnection = Any


def _strip_block_comments(sql_text: str) -> str:
    return re.sub(r"/\*.*?\*/", "", sql_text, flags=re.DOTALL)


def _split_statements(sql_text: str, delimiter: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []

    in_single_quote = False
    in_double_quote = False
    index = 0
    text_length = len(sql_text)

    while index < text_length:
        char = sql_text[index]
        prev_char = sql_text[index - 1] if index > 0 else ""

        if char == "'" and not in_double_quote and prev_char != "\\":
            in_single_quote = not in_single_quote
        elif char == '"' and not in_single_quote and prev_char != "\\":
            in_double_quote = not in_double_quote

        if (
            not in_single_quote
            and not in_double_quote
            and sql_text.startswith(delimiter, index)
        ):
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
            index += len(delimiter)
            continue

        current.append(char)
        index += 1

    trailing = "".join(current).strip()
    if trailing:
        statements.append(trailing)

    return statements


def execute_sql_script(
    connection: MySQLConnection,
    script_path: Path,
    database_name: str,
    database_collation: str,
    delimiter: str = ";",
) -> None:
    sql_text = script_path.read_text(encoding="utf-8")
    sql_text = sql_text.replace("@database@", database_name)
    sql_text = sql_text.replace("@dbcollation@", database_collation)
    sql_text = _strip_block_comments(sql_text)

    statements = _split_statements(sql_text, delimiter=delimiter)

    with connection.cursor() as cursor:
        for statement in statements:
            trimmed = statement.strip()
            if not trimmed:
                continue
            cursor.execute(trimmed)
    connection.commit()
