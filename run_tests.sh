#!/bin/sh
# Runs every test. The two browser tests are skipped if Playwright is not installed.
set -e
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
$PY tests/test_logic.py
$PY tests/test_js.py
$PY tests/test_api.py
if $PY -c "import playwright" 2>/dev/null; then
  $PY tests/test_browser.py
  $PY tests/test_browser_edge.py
else
  echo "(skipping browser tests: pip install playwright && playwright install chromium)"
fi
echo "ALL TEST SUITES PASSED"
