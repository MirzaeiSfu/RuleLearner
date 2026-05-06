# FactorBase Python Migration (Initial-Source Branch)

Branch target: `migration/python-factorbase-v1`  
Base commit: `3845d99` (initial import)

## Step 0: Branching

This migration branch is created directly from the initial import commit and isolated from patched history.

## Step 1: Setup/Metadata in Python

Implemented in:

- `python_factorbase/fb_pipeline/setup_pipeline.py`
- `python_factorbase/fb_pipeline/sql_runner.py`
- `python_factorbase/fb_pipeline/db.py`

Execution order matches Java `setupDatabase()`:

1. `initialize_databases.sql`
2. `metadata.sql`
3. `metadata_storedprocedures.sql`
4. `CALL find_values()`
5. `latticegenerator_initialize.sql`
6. `logging.sql`
7. `latticegenerator_initialize_local.sql`
8. `latticegenerator_populate.sql`
9. `transfer_initialize.sql`
10. `transfer_cascade.sql`
11. `modelmanager_initialize.sql`
12. `metaqueries_initialize.sql`
13. `metaqueries_populate.sql`
14. `metaqueries_RChain.sql`

## Step 2: FMT bootstrap in Python

Implemented in:

- `python_factorbase/fb_pipeline/fmt_pipeline.py`

Current procedure-driven FMT bootstrap:

1. `CALL cascadeFS()`
2. `CALL populateLattice()`
3. `CALL populateMQ()`
4. `CALL populateMQRChain()`

## Step 3: Standalone BN learner jar (kept in Java)

Implemented new module:

- `code/bnrunner/pom.xml`
- `code/bnrunner/src/main/java/ca/sfu/cs/bnrunner/app/RunBNLearner.java`

Jar build:

```bash
cd code
mvn -pl bnrunner -am -DskipTests package
```

BN jar accepts:

- `--input-tsv`
- `--output-edges`
- `--required-edges` (optional)
- `--forbidden-edges` (optional)
- `--counts-column` (optional)
- `--discrete` (optional)

## Python CLI

Entry point:

- `python python_factorbase/run.py ...`

Commands:

- `setup`
- `fmt`
- `setup-and-fmt`
- `bn-learn`

