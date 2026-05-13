#!/usr/bin/env python3
from __future__ import annotations

import subprocess
from pathlib import Path


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


def main() -> None:
    if not BASELINE_CONFIG.exists():
        raise FileNotFoundError(f"Missing config: {BASELINE_CONFIG}")
    if not PYTHON_CONFIG.exists():
        raise FileNotFoundError(f"Missing config: {PYTHON_CONFIG}")

    baseline_cfg = parse_properties(BASELINE_CONFIG)
    python_cfg = parse_properties(PYTHON_CONFIG)

    command = [
        "python",
        "python_factorbase/scripts/compare_original_vs_python.py",
        "--baseline-dbname",
        baseline_cfg["dbname"],
        "--python-dbname",
        python_cfg["dbname"],
        "--outdir",
        "compare_runs/unielwin_3line",
    ]

    subprocess.run(command, cwd=ROOT_DIR, check=True)
    print(f"Summary written to: {SUMMARY_PATH}")


if __name__ == "__main__":
    main()
