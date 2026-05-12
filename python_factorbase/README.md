# Python FactorBase Orchestration

This folder contains the Python orchestration path for migrating FactorBase while keeping BN structure learning in Java.
The Python code no longer launches `factorbase-1.0-SNAPSHOT.jar`.
It only calls the standalone BN learner jar (`bnrunner`).
For Python execution, there is one canonical Java learner jar path:
`code/bnrunner/target/bnrunner-1.0-SNAPSHOT.jar`.

## BN learner provenance (double-checked)

In `code/factorbase/src/main/java` there are two relevant trees:

- `edu/cmu/tetrad`: Tetrad learner classes (package name from CMU).
- `ca/sfu/cs/factorbase`: FactorBase integration/wrapper classes.

The standalone jar used by Python calls:

- `ca.sfu.cs.bnrunner.app.RunBNLearner` (entry point)
- which calls `ca.sfu.cs.factorbase.jbn.BayesNet_Learning_main.tetradLearner(...)`
- which uses `edu.cmu.tetrad.search.GesCT` and `edu.cmu.tetrad.search.PatternToDag`.

So the learner path is the FactorBase-integrated/modified path (SFU wrapper + local `edu/cmu` classes packaged in this repo), not a pure external CMU binary.

## Scope of this phase

1. Step 1 (`setup`): metadata + setup SQL pipeline in Python.
2. Step 2 (`fmt`): FMT bootstrap stored procedures in Python.
3. Step 3 (`bn-learn`): call standalone Java BN learner jar from Python.

## Install Python dependency

```bash
pip install -r python_factorbase/requirements.txt
```

## Build the standalone BN learner jar

```bash
bash python_factorbase/scripts/build_bn_learner_jar.sh
```

Output jar (used by Python by default):

`code/bnrunner/target/bnrunner-1.0-SNAPSHOT.jar`

## Run setup + FMT from Python

From repo root:

```bash
python python_factorbase/run.py --config config.cfg setup
python python_factorbase/run.py --config config.cfg fmt
```

Or:

```bash
python python_factorbase/run.py --config config.cfg setup-and-fmt
```

By default, `setup` and `setup-and-fmt` run in GraphVAE-safe mode and drop
`<dbname>.run_metadata` before setup scans source tables. This avoids repeated-run
failures when long metadata values overflow FactorBase setup staging columns.

Python setup fails fast if the source database has no `FOREIGN KEY` constraints,
with an explicit error message.

If you need to keep the source `run_metadata` table untouched:

```bash
python python_factorbase/run.py --config config.cfg setup --keep-run-metadata
python python_factorbase/run.py --config config.cfg setup-and-fmt --keep-run-metadata
```

## Run BN learner from Python

```bash
python python_factorbase/run.py --config config.cfg bn-learn \
  --input-tsv /path/to/input_ct.tsv \
  --output-edges /path/to/output_edges.tsv
```

Optional constraints:

```bash
--required-edges /path/to/required_edges.tsv \
--forbidden-edges /path/to/forbidden_edges.tsv
```

Or run everything in one command (setup + FMT + BN learner):

```bash
python python_factorbase/run.py --config config.cfg setup-and-fmt-and-bn-learn \
  --input-tsv /path/to/input_ct.tsv \
  --output-edges /path/to/output_edges.tsv
```

To use another jar path, pass `--jar /path/to/another-bnrunner.jar`.

Edge file format (tab or comma separated):

```text
parent    child
X         Y
          Z
```
