#!/bin/bash
set -euo pipefail

# check-scan-results.sh - Check Snyk scan results for vulnerabilities
# Inputs: snyk_results.json (file)
# Outputs: vulnerabilities_found (GitHub output)

if [ ! -f snyk_results.json ]; then
    echo "Error: snyk_results.json not found"
    echo "vulnerabilities_found=false" >> $GITHUB_OUTPUT
    exit 0
fi

# Check for scan errors
if jq -e .error snyk_results.json > /dev/null 2>&1; then
    echo "Snyk scan returned an error"
    echo "vulnerabilities_found=true" >> $GITHUB_OUTPUT
    exit 0
fi

# Count high/critical vulnerabilities
HIGH_CRITICAL=$(jq '[.vulnerabilities[]? | select(.severity == "high" or .severity == "critical")] | length' snyk_results.json 2>/dev/null || echo "0")

if [ "$HIGH_CRITICAL" -gt 0 ]; then
    echo "Found $HIGH_CRITICAL high/critical vulnerabilities"
    echo "vulnerabilities_found=true" >> $GITHUB_OUTPUT
else
    echo "No high/critical vulnerabilities found"
    echo "vulnerabilities_found=false" >> $GITHUB_OUTPUT
fi
