from __future__ import annotations

from pathlib import Path

from .config import FBConfig
from .db import call_procedure, connect, use_database
from .sql_runner import execute_sql_script


class SetupPipeline:
    def __init__(self, config: FBConfig, scripts_dir: Path):
        self.config = config
        self.scripts_dir = scripts_dir

    def _script(self, file_name: str) -> Path:
        return self.scripts_dir / file_name

    def run(self) -> None:
        connection = connect(self.config, database=self.config.dbname)
        try:
            execute_sql_script(
                connection,
                self._script("initialize_databases.sql"),
                self.config.dbname,
                self.config.dbcollation,
            )

            use_database(connection, self.config.setup_db)
            execute_sql_script(
                connection,
                self._script("metadata.sql"),
                self.config.dbname,
                self.config.dbcollation,
            )
            execute_sql_script(
                connection,
                self._script("metadata_storedprocedures.sql"),
                self.config.dbname,
                self.config.dbcollation,
                delimiter="//",
            )
            call_procedure(connection, "find_values")

            execute_sql_script(
                connection,
                self._script("latticegenerator_initialize.sql"),
                self.config.dbname,
                self.config.dbcollation,
            )

            use_database(connection, self.config.bn_db)
            execute_sql_script(
                connection,
                self._script("logging.sql"),
                self.config.dbname,
                self.config.dbcollation,
            )
            execute_sql_script(
                connection,
                self._script("latticegenerator_initialize_local.sql"),
                self.config.dbname,
                self.config.dbcollation,
            )
            execute_sql_script(
                connection,
                self._script("latticegenerator_populate.sql"),
                self.config.dbname,
                self.config.dbcollation,
                delimiter="//",
            )
            execute_sql_script(
                connection,
                self._script("transfer_initialize.sql"),
                self.config.dbname,
                self.config.dbcollation,
            )
            execute_sql_script(
                connection,
                self._script("transfer_cascade.sql"),
                self.config.dbname,
                self.config.dbcollation,
                delimiter="//",
            )
            execute_sql_script(
                connection,
                self._script("modelmanager_initialize.sql"),
                self.config.dbname,
                self.config.dbcollation,
            )
            execute_sql_script(
                connection,
                self._script("metaqueries_initialize.sql"),
                self.config.dbname,
                self.config.dbcollation,
            )
            execute_sql_script(
                connection,
                self._script("metaqueries_populate.sql"),
                self.config.dbname,
                self.config.dbcollation,
                delimiter="//",
            )
            execute_sql_script(
                connection,
                self._script("metaqueries_RChain.sql"),
                self.config.dbname,
                self.config.dbcollation,
                delimiter="//",
            )
        finally:
            connection.close()

