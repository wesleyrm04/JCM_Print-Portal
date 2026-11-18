#!/bin/sh
# Runs every test. The two browser tests are skipped if Playwright is not installed.
set -e
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
$PY tests/test_logic.py
$PY tests/test_security.py
$PY tests/test_middleware.py
$PY tests/test_app_rules.py
$PY tests/test_js.py
$PY tests/test_api.py
$PY tests/test_api_security.py
$PY tests/test_share.py
$PY tests/test_api_share.py
$PY tests/test_catalog.py
$PY tests/test_api_catalog.py
if [ -n "$JCM_POS_SRC" ]; then $PY tests/test_pos_parity.py; else echo "(skipping POS price parity test: set JCM_POS_SRC=/path/to/JCM_PrintPOS to run it)"; fi
if $PY -c "import playwright" 2>/dev/null; then
  $PY tests/test_browser.py
  $PY tests/test_browser_edge.py
else
  echo "(skipping browser tests: pip install playwright && playwright install chromium)"
fi
echo "ALL TEST SUITES PASSED"
