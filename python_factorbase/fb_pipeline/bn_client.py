from __future__ import annotations

import subprocess
from pathlib import Path


def run_bn_learner(
    jar_path: Path,
    input_tsv: Path,
    output_edges: Path,
    counts_column: str = "MULT",
    discrete: bool = True,
    required_edges: Path | None = None,
    forbidden_edges: Path | None = None,
    java_bin: str = "java",
) -> None:
    command = [
        java_bin,
        "-jar",
        str(jar_path),
        "--input-tsv",
        str(input_tsv),
        "--output-edges",
        str(output_edges),
        "--counts-column",
        counts_column,
        "--discrete",
        str(discrete).lower(),
    ]

    if required_edges is not None:
        command.extend(["--required-edges", str(required_edges)])

    if forbidden_edges is not None:
        command.extend(["--forbidden-edges", str(forbidden_edges)])

    subprocess.run(command, check=True)

