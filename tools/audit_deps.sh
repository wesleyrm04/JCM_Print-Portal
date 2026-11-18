#!/bin/sh
# Checks the installed packages against the public vulnerability database. Needs internet once.
cd "$(dirname "$0")/.."
PY=${PYTHON:-venv/bin/python}
$PY -m pip install --quiet pip-audit && $PY -m pip_audit -r requirements.txt
