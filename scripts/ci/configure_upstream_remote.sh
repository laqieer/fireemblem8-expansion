#!/usr/bin/env bash
set -euo pipefail
CANONICAL_URL=$(python3 -c "from scripts.upstream_port import constants; print(constants.CANONICAL_UPSTREAM_URL)")
REMOTE_NAME=$(python3 -c "from scripts.upstream_port import constants; print(constants.DEFAULT_REMOTE_NAME)")

if git remote get-url "$REMOTE_NAME" >/dev/null 2>&1; then
    EXISTING_URL=$(git remote get-url "$REMOTE_NAME")
    if [ "$EXISTING_URL" != "$CANONICAL_URL" ]; then
        echo "::error::remote '$REMOTE_NAME' URL '$EXISTING_URL' does not match pinned canonical URL '$CANONICAL_URL'"
        exit 1
    fi
else
    git remote add "$REMOTE_NAME" "$CANONICAL_URL"
fi

echo "remote_name=$REMOTE_NAME" >> "$GITHUB_OUTPUT"
echo "canonical_url=$CANONICAL_URL" >> "$GITHUB_OUTPUT"
