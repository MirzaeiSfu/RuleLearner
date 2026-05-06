from __future__ import annotations

from .config import FBConfig
from .db import call_procedure, connect, use_database


class FMTPipeline:
    def __init__(self, config: FBConfig):
        self.config = config

    def run(self) -> None:
        connection = connect(self.config, database=self.config.bn_db)
        try:
            use_database(connection, self.config.bn_db)

            # Step 2: FMT bootstrap from SQL/stored-procedure pipeline.
            call_procedure(connection, "cascadeFS")
            call_procedure(connection, "populateLattice")
            call_procedure(connection, "populateMQ")
            call_procedure(connection, "populateMQRChain")
        finally:
            connection.close()

