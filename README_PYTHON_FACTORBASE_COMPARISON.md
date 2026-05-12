# Python FactorBase vs Original FactorBase (Run + Compare Guide)

This guide shows:

1. Which JAR the Python code uses for rule/BN learning.
2. How to run the original Java FactorBase baseline.
3. How to run the Python-based FactorBase flow.
4. How to compare outputs (table counts + learned edges).

## 1) Which JAR is used in the new Python code?

- Python now uses only the standalone BN learner jar:
  - command: `python python_factorbase/run.py ... bn-learn --jar ...`
  - canonical jar path: `code/bnrunner/target/bnrunner-1.0-SNAPSHOT.jar`
  - source: `code/bnrunner/src/main/java/...`

`factorbase-1.0-SNAPSHOT.jar` is not used by Python orchestration commands.

Call-chain check:

- `ca.sfu.cs.bnrunner.app.RunBNLearner` (bnrunner main class)
- calls `ca.sfu.cs.factorbase.jbn.BayesNet_Learning_main`
- which invokes `edu.cmu.tetrad.search.GesCT` + `PatternToDag`

So Python uses the FactorBase-integrated learner path (SFU wrapper + local `edu/cmu` classes packaged from this repo).

## 2) Prerequisites

- Java 8+ and Maven
- Python 3.9+
- MySQL/MariaDB reachable from `config.cfg`
- From repo root:

```bash
pip install -r python_factorbase/requirements.txt
```

## 3) Build jars from source (recommended before comparison)

```bash
bash python_factorbase/scripts/build_bn_learner_jar.sh
```

Expected artifact:

- `code/bnrunner/target/bnrunner-1.0-SNAPSHOT.jar`

## 4) Prepare two configs (to avoid run-to-run interference)

Create two config files with identical DB/server settings except `dbname`:

- `config.baseline.cfg`  (example `dbname = grid_undi_baseline`)
- `config.python.cfg`    (example `dbname = grid_undi_python`)

Both should point to copies of the same source dataset.

## 5) Run original Java FactorBase baseline

```bash
java -Dconfig=config.baseline.cfg -jar code/factorbase/target/factorbase-1.0-SNAPSHOT.jar
```

## 6) Run Python-based flow (setup + FMT only)

```bash
python python_factorbase/run.py --config config.python.cfg setup-and-fmt
```

Notes:

- Python setup uses a GraphVAE-safe default that drops `<dbname>.run_metadata` before setup scan.
- Python setup guards against missing source foreign keys.

## 7) Run BN learner jar from Python

Use the same learner jar for both pipelines:

```bash
python python_factorbase/run.py --config config.python.cfg bn-learn \
  --input-tsv /path/to/input_ct.tsv \
  --output-edges /path/to/output_edges_python.tsv
```

Or one-shot:

```bash
python python_factorbase/run.py --config config.python.cfg setup-and-fmt-and-bn-learn \
  --input-tsv /path/to/input_ct.tsv \
  --output-edges /path/to/output_edges_python.tsv
```

## 8) Compare outputs

### A) Compare setup/count artifacts (database side)

Baseline CT schema:

```sql
SELECT COUNT(*) AS ct_table_count
FROM information_schema.tables
WHERE table_schema = 'grid_undi_baseline_CT';
```

Python CT schema:

```sql
SELECT COUNT(*) AS ct_table_count
FROM information_schema.tables
WHERE table_schema = 'grid_undi_python_CT';
```

### B) Compare learned edges using the same BN learner (recommended)

1. Export the same CT table from each pipeline to TSV (same table name in both DBs).
2. Run `bn-learn` twice (once per TSV).
3. Compare edge files:

```bash
sort output_edges_baseline.tsv > baseline.sorted.tsv
sort output_edges_python.tsv > python.sorted.tsv
diff -u baseline.sorted.tsv python.sorted.tsv
```

If `diff` is empty, learned edges are identical for that CT input.
