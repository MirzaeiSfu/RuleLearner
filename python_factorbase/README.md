# Python FactorBase Orchestration

This folder contains the Python orchestration path for migrating FactorBase while keeping BN structure learning in Java.

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
cd code
mvn -pl bnrunner -am -DskipTests package
```

Output jar:

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

## Run BN learner from Python

```bash
python python_factorbase/run.py bn-learn \
  --jar code/bnrunner/target/bnrunner-1.0-SNAPSHOT.jar \
  --input-tsv /path/to/input_ct.tsv \
  --output-edges /path/to/output_edges.tsv
```

Optional constraints:

```bash
--required-edges /path/to/required_edges.tsv \
--forbidden-edges /path/to/forbidden_edges.tsv
```

Edge file format (tab or comma separated):

```text
parent    child
X         Y
          Z
```

