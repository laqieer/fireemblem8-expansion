#!/usr/bin/env bash
set +e
STATE_PATH="config/upstream-port-state.json"
if [ ! -f "$STATE_PATH" ]; then
    echo "## Upstream Port Drift Scan" >> "$GITHUB_STEP_SUMMARY"
    echo "" >> "$GITHUB_STEP_SUMMARY"
    echo "no state file found at $STATE_PATH" >> "$GITHUB_STEP_SUMMARY"
    echo "exit_code=3" >> "$GITHUB_OUTPUT"
    exit 0
fi

RECORDED_LAST_PORTED_SHA=$(python3 -c "import json;print(json.load(open('$STATE_PATH'))['last_ported']['sha'])")
FRESH_REF=$(python3 -c "import json;print(json.load(open('$STATE_PATH'))['last_ported']['ref'])")

python3 -m scripts.upstream_port drift --ref "$FRESH_REF" --format json > drift-report.json
code=$?

python3 -m scripts.upstream_port scan --ref "$FRESH_REF" --format json > scan-report.json 2> scan-report.err
scan_code=$?

append_report_preview() {
    python3 -c '
import sys

limit = 256 * 1024
with open(sys.argv[1], "rb") as stream:
    preview = stream.read(limit + 1)
print(preview[:limit].decode("utf-8", errors="ignore"), end="")
if len(preview) > limit:
    print("\n[truncated; full " + sys.argv[1]
          + " is in the upstream-port-drift-report artifact]")
' "$1"
}

{
    echo "## Upstream Port Drift Scan"
    echo ""
    echo "Recorded boundary: \`$FRESH_REF\` @ \`$RECORDED_LAST_PORTED_SHA\`"
    echo "Freshly fetched tip of \`$FRESH_REF\` was scanned/drift-checked below --"
    echo "this run explicitly fetched the pinned canonical remote first (see the"
    echo "preceding job steps), so a moved ref here reflects a real upstream change,"
    echo "not just this repo's own history."
    echo ""
    echo "### drift"
    echo '```json'
    append_report_preview drift-report.json
    echo '```'
    echo ""
    echo "### scan (best-effort; new commits since the recorded last_ported boundary)"
    if [ "$scan_code" -eq 0 ]; then
        echo '```json'
        append_report_preview scan-report.json
        echo '```'
    else
        echo "scan could not enumerate individual commits (exit $scan_code); see drift"
        echo "output above for the authoritative drift/integrity signal:"
        echo '```text'
        append_report_preview scan-report.err
        echo '```'
    fi
} >> "$GITHUB_STEP_SUMMARY"

echo "exit_code=$code" >> "$GITHUB_OUTPUT"
exit 0
