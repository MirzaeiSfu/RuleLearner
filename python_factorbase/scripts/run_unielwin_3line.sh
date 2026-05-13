#!/usr/bin/env bash
java -Dconfig=python_factorbase/configs/unielwin_baseline.cfg -jar code/factorbase/target/factorbase-1.0-SNAPSHOT.jar
python python_factorbase/pyfactorbase.py -Dconfig=python_factorbase/configs/unielwin_python.cfg -jarlearner code/bnrunner/target/bnrunner-1.0-SNAPSHOT.jar
python python_factorbase/scripts/demo_unielwin_compare.py
