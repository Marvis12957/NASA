#!/bin/bash
# run_tests.sh — run the agentctl test suite and write tests/TEST_REPORT.md.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
REPORT="tests/TEST_REPORT.md"
OUT=$(python3 tests/test_agentctl.py -v 2>&1)
CODE=$?
PASSED=$(echo "$OUT" | grep -c " \.\.\. ok")
FAILED=$(echo "$OUT" | grep -cE " \.\.\. (FAIL|ERROR)")
{
    echo "# Test Report — agentctl"
    echo ""
    echo "Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ) (UTC)"
    echo "Command: \`python3 tests/test_agentctl.py -v\`"
    echo ""
    echo "## Summary"
    echo ""
    echo "- Passed: $PASSED"
    echo "- Failed: $FAILED"
    echo "- Exit code: $CODE"
    echo ""
    echo "## Per-test results"
    echo ""
    echo '```'
    echo "$OUT" | grep -E " \.\.\. (ok|FAIL|ERROR)"
    echo '```'
    echo ""
    echo "## Full output"
    echo ""
    echo '```'
    echo "$OUT" | tail -30
    echo '```'
} > "$REPORT"
echo "$OUT" | grep -E " \.\.\. (ok|FAIL|ERROR)"
echo "---"
echo "Passed: $PASSED, Failed: $FAILED (report: $REPORT)"
exit $CODE
