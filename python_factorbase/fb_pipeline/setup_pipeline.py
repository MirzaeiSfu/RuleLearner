from __future__ import annotations

from pathlib import Path

from .config import FBConfig
from .db import call_procedure, connect, ensure_schema_has_foreign_keys, use_database
from .sql_runner import execute_sql_script


class SetupPipeline:
    def __init__(
        self,
        config: FBConfig,
        scripts_dir: Path,
        drop_run_metadata: bool = True,
    ):
        self.config = config
        self.scripts_dir = scripts_dir
        self.drop_run_metadata = drop_run_metadata

    def _script(self, file_name: str) -> Path:
        return self.scripts_dir / file_name

    def _drop_run_metadata_table(self, connection) -> None:
        escaped_dbname = self.config.dbname.replace("`", "``")
        with connection.cursor() as cursor:
            cursor.execute(f"DROP TABLE IF EXISTS `{escaped_dbname}`.`run_metadata`;")
        connection.commit()

    def run(self) -> None:
        connection = connect(self.config, database=self.config.dbname)
        try:
            ensure_schema_has_foreign_keys(connection, self.config.dbname)

            if self.drop_run_metadata:
                # GraphVAE-safe behavior: remove pipeline metadata table before
                # setup scans source tables (avoids long VALUE overflow in setup).
                self._drop_run_metadata_table(connection)

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
