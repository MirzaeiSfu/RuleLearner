from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    return normalized in {"1", "true", "yes", "y", "on"}


def _parse_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value.strip())
    except ValueError:
        return default


def _parse_properties(path: Path) -> dict[str, str]:
    properties: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        properties[key.strip()] = value.strip()
    return properties


@dataclass(frozen=True)
class FBConfig:
    dbaddress: str
    dbname: str
    dbusername: str
    dbpassword: str
    dbcollation: str
    automatic_setup: bool
    compute_kld: bool
    continuous: bool
    link_correlations: bool
    use_local_ct: bool
    skip_parameter_learning: bool
    counting_strategy: int
    logging_level: str

    @classmethod
    def from_file(cls, config_path: Path) -> "FBConfig":
        values = _parse_properties(config_path)
        return cls(
            dbaddress=values["dbaddress"],
            dbname=values["dbname"],
            dbusername=values["dbusername"],
            dbpassword=values.get("dbpassword", ""),
            dbcollation=values.get("dbcollation", "latin1_swedish_ci"),
            automatic_setup=_parse_bool(values.get("AutomaticSetup"), True),
            compute_kld=_parse_bool(values.get("ComputeKLD"), False),
            continuous=_parse_bool(values.get("Continuous"), False),
            link_correlations=_parse_bool(values.get("LinkCorrelations"), True),
            use_local_ct=_parse_bool(values.get("UseLocal_CT"), False),
            skip_parameter_learning=_parse_bool(values.get("SkipParameterLearning"), False),
            counting_strategy=_parse_int(values.get("CountingStrategy"), 0),
            logging_level=values.get("LoggingLevel", "info"),
        )

    @property
    def host(self) -> str:
        parsed = urlparse(self.dbaddress)
        return parsed.hostname or "127.0.0.1"

    @property
    def port(self) -> int:
        parsed = urlparse(self.dbaddress)
        return parsed.port or 3306

    @property
    def setup_db(self) -> str:
        return f"{self.dbname}_setup"

    @property
    def bn_db(self) -> str:
        return f"{self.dbname}_BN"

    @property
    def ct_db(self) -> str:
        return f"{self.dbname}_CT"
