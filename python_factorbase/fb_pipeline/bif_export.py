from __future__ import annotations

from pathlib import Path

from .config import FBConfig
from .db import quote_identifier


STRUCTURE_BIF_HEADER = """<?xml version="1.0"?>
<!-- DTD for the XMLBIF 0.3 format -->
<!DOCTYPE BIF [
\t<!ELEMENT BIF ( NETWORK )*>
\t\t<!ATTLIST BIF VERSION CDATA #REQUIRED>
\t<!ELEMENT NETWORK ( NAME, ( PROPERTY | VARIABLE | DEFINITION )* )>
\t<!ELEMENT NAME (#PCDATA)>
\t<!ELEMENT VARIABLE ( NAME, ( OUTCOME |  PROPERTY )* ) >
\t\t<!ATTLIST VARIABLE TYPE (nature|decision|utility) "nature">
\t<!ELEMENT OUTCOME (#PCDATA)>
\t<!ELEMENT DEFINITION ( FOR | GIVEN | TABLE | PROPERTY )* >
\t<!ELEMENT FOR (#PCDATA)>
\t<!ELEMENT GIVEN (#PCDATA)>
\t<!ELEMENT TABLE (#PCDATA)>
\t<!ELEMENT PROPERTY (#PCDATA)>
]>

"""

PARAMETER_BIF_HEADER = """<?xml version="1.0" encoding="UTF-8"?>
<BIF VERSION="0.3"  xmlns="http://www.cs.ubc.ca/labs/lci/fopi/ve/XMLBIFv0_3"
xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
xsi:schemaLocation="http://www.cs.ubc.ca/labs/lci/fopi/ve/XMLBIFv0_3 http://www.cs.ubc.ca/labs/lci/fopi/ve/XMLBIFv0_3/XMLBIFv0_3.xsd">
"""


def _write_structure_network_begin(name: str) -> str:
    return f'<BIF VERSION="0.3">\n<NETWORK>\n<NAME>{name}</NAME>\n'


def _write_parameter_network_begin(name: str) -> str:
    return f"<NETWORK>\n<NAME>{name}</NAME>\n"


def _write_network_end() -> str:
    return "</NETWORK>\n</BIF>\n"


def _write_structure_variable(variable: str) -> str:
    return (
        "<VARIABLE TYPE=\"nature\">\n"
        f"\t<NAME>{variable}</NAME>\n"
        "</VARIABLE>\n"
    )


def _write_structure_definition(for_variable: str, given_variables: list[str]) -> str:
    lines = ["<DEFINITION>\n", f"\t<FOR>{for_variable}</FOR>\n"]
    for given in given_variables:
        lines.append(f"\t<GIVEN>{given}</GIVEN>\n")
    lines.append("</DEFINITION>\n")
    return "".join(lines)


def _write_parameter_variable(variable: str, outcomes: list[str], x: int, y: int) -> str:
    lines = ["<VARIABLE TYPE=\"nature\">\n", f"\t<NAME>{variable}</NAME>\n"]
    for outcome in outcomes:
        lines.append(f"\t<OUTCOME>{outcome}</OUTCOME>\n")
    lines.append(f"\t<PROPERTY> position=({x},{y})</PROPERTY>\n")
    lines.append("</VARIABLE>\n")
    return "".join(lines)


def _write_parameter_definition(for_variable: str, given_variables: list[str], probabilities: str) -> str:
    lines = ["<DEFINITION>\n", f"\t<FOR>{for_variable}</FOR>\n"]
    for given in given_variables:
        lines.append(f"\t<GIVEN>{given}</GIVEN>\n")
    lines.append(f"\t<TABLE>{probabilities}</TABLE>\n")
    lines.append("</DEFINITION>\n")
    return "".join(lines)


def create_final_path_bayesnets(connection, largest_rchain: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS Final_Path_BayesNets")
        cursor.execute(
            "CREATE TABLE Final_Path_BayesNets "
            "(SELECT * FROM Path_BayesNets WHERE Rchain = %s AND parent <> '')",
            (largest_rchain,),
        )
        cursor.execute(
            "ALTER TABLE Final_Path_BayesNets ADD PRIMARY KEY (Rchain, child, parent)"
        )
    connection.commit()


def export_structure_bifs(
    connection,
    config: FBConfig,
    max_number_of_members: int,
    output_root: Path | None = None,
) -> None:
    root = output_root or Path(config.dbname) / "res"
    root.mkdir(parents=True, exist_ok=True)

    with connection.cursor() as cursor:
        cursor.execute("SELECT name FROM lattice_set WHERE length = %s", (max_number_of_members,))
        rchains = [str(row[0]) for row in cursor.fetchall()]

    for rchain in rchains:
        export_structure_bif(connection, root / f"{rchain}.xml", "Rchain", "Path_BayesNets", rchain)


def export_structure_bif(
    connection,
    output_path: Path,
    id_name: str,
    table_name: str,
    rchain: str,
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT child, parent FROM {quote_identifier(table_name)} "
            f"WHERE {quote_identifier(id_name)} = %s",
            (rchain,),
        )
        rows = [(str(row[0]), "" if row[1] is None else str(row[1])) for row in cursor.fetchall()]

    variables: list[str] = []
    connections: list[tuple[str, str]] = []
    for child, parent in rows:
        if child not in variables:
            variables.append(child)
        connections.append((child, parent))

    parts = [STRUCTURE_BIF_HEADER, _write_structure_network_begin(rchain)]
    for variable in variables:
        parts.append(_write_structure_variable(variable))

    for variable in variables:
        parents = [parent for child, parent in connections if child == variable and parent]
        if parents:
            parts.append(_write_structure_definition(variable, parents))

    parts.append(_write_network_end())
    output_path.write_text("".join(parts), encoding="utf-8")


def generate_parameter_bif(
    connection,
    config: FBConfig,
    output_path: Path | None = None,
) -> None:
    target = output_path or Path(f"Bif_{config.dbname}.xml")

    with connection.cursor() as cursor:
        cursor.execute("SELECT short_rnid, orig_rnid FROM lattice_mapping")
        name_mapping = {str(row[0]): str(row[1]) for row in cursor.fetchall()}
        cursor.execute("SELECT DISTINCT child FROM Path_BayesNets")
        variables = [str(row[0]) for row in cursor.fetchall()]
        cursor.execute(
            "SELECT DISTINCT lattice_set.name "
            "FROM lattice_membership, lattice_set "
            "WHERE length = (SELECT MAX(length) FROM lattice_set)"
        )
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("Unable to determine the largest relationship chain for BIF export.")
        largest_rchain = str(row[0])

    parts = [PARAMETER_BIF_HEADER, _write_parameter_network_begin(config.dbname)]

    x = 6000
    y = 4000
    row_counter = 0
    outcome_counts: dict[str, int] = {}

    with connection.cursor() as cursor:
        for variable in variables:
            cursor.execute(
                f"SELECT DISTINCT {quote_identifier(variable)} "
                f"FROM {quote_identifier(variable + '_CP_smoothed')} "
                f"ORDER BY {quote_identifier(variable)}"
            )
            outcomes = ["" if value[0] is None else str(value[0]) for value in cursor.fetchall()]
            outcome_counts[variable] = len(outcomes)
            parts.append(
                _write_parameter_variable(
                    name_mapping.get(variable, variable),
                    outcomes,
                    x,
                    y,
                )
            )
            row_counter += 1
            x += 200
            if row_counter == 3:
                row_counter = 0
                y += 200
                x -= 600

        for variable in variables:
            cursor.execute(
                "SELECT DISTINCT parent "
                "FROM Path_BayesNets "
                "WHERE child = %s AND parent <> '' AND Rchain = %s",
                (variable, largest_rchain),
            )
            given = [str(row[0]) for row in cursor.fetchall()]

            probability_chunks: list[str] = []
            if given:
                order_by = ", ".join([quote_identifier(parent) for parent in given] + [quote_identifier(variable)])
                cursor.execute(
                    f"SELECT CP FROM {quote_identifier(variable + '_CP_smoothed')} ORDER BY {order_by}"
                )
            else:
                cursor.execute(
                    f"SELECT CP FROM {quote_identifier(variable + '_CP_smoothed')} "
                    f"ORDER BY {quote_identifier(variable)}"
                )
            for row in cursor.fetchall():
                probability_chunks.append(f" {row[0]}")

            mapped_given = [name_mapping.get(parent, parent) for parent in given]
            parts.append(
                _write_parameter_definition(
                    name_mapping.get(variable, variable),
                    mapped_given,
                    "".join(probability_chunks),
                )
            )

    parts.append(_write_network_end())
    target.write_text("".join(parts), encoding="utf-8")
