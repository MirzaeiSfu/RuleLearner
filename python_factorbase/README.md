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
4. Counterpart mode (`factorbase-counterpart` / `pyfactorbase.py`): run the Java-like FactorBase flow in Python, including structure learning, parameter learning, optional KLD generation, and BIF export.

## Full counterpart mode (config-driven)

For a jar-like run that reads dataset/db names from `config.cfg` and follows the Java
FactorBase phase order, use:

```bash
python python_factorbase/pyfactorbase.py -Dconfig=config.cfg -jarlearner code/bnrunner/target/bnrunner-1.0-SNAPSHOT.jar
```

Equivalent CLI subcommand:

```bash
python python_factorbase/run.py -Dconfig=config.cfg factorbase-counterpart -jarlearner code/bnrunner/target/bnrunner-1.0-SNAPSHOT.jar
```

This counterpart flow now:

- learns `Entity_BayesNets`, `Path_BayesNets`, and `Final_Path_BayesNets`
- exports structure BIF files under `<dbname>/res/`
- computes CP / score tables in `<dbname>_BN`
- runs KLD generation when `ComputeKLD=1`
- writes the final parameterized BIF to `Bif_<dbname>.xml`

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

## Quick parity runner (original Java vs Python flow)

Use this helper to run both flows and print whether outputs match:

```bash
python python_factorbase/scripts/compare_original_vs_python.py \
  --baseline-config config.baseline.cfg \
  --python-config config.python.cfg
```

The script writes comparison artifacts under:

`compare_runs/latest/`

including `summary.txt`, exported CT TSV files, and learned edge TSV files.
For the Python-side output, use the full counterpart command (`pyfactorbase.py`) so
the Python run follows the Java-like database flow and writes learned structure,
parameter tables, and BIF outputs.

## 3-line demo (hardcoded unielwin)

This demo is hardcoded for the `unielwin` setup and uses:

- baseline dbname: `unielwin_baseline`
- python dbname: `unielwin_python`
- baseline config: `python_factorbase/configs/unielwin_baseline.cfg`
- python config: `python_factorbase/configs/unielwin_python.cfg`

Before running, make sure:

- both source databases exist: `unielwin_baseline` and `unielwin_python`
- both configs contain valid local DB credentials

Top-level bash file (exactly 3 command lines):

```bash
bash python_factorbase/scripts/run_unielwin_3line.sh
```

Equivalent 3 commands inside that bash file:

```bash
java -Dconfig=python_factorbase/configs/unielwin_baseline.cfg -jar code/factorbase/target/factorbase-1.0-SNAPSHOT.jar
python python_factorbase/pyfactorbase.py -Dconfig=python_factorbase/configs/unielwin_python.cfg -jarlearner code/bnrunner/target/bnrunner-1.0-SNAPSHOT.jar
python python_factorbase/scripts/demo_unielwin_compare.py
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

For the full Java-like counterpart flow, prefer:

```bash
python python_factorbase/run.py -Dconfig=config.cfg factorbase-counterpart \
  -jarlearner code/bnrunner/target/bnrunner-1.0-SNAPSHOT.jar
```

To use another jar path, pass `--jar /path/to/another-bnrunner.jar`.

Edge file format (tab or comma separated):

```text
parent    child
X         Y
          Z
```
