#!/bin/bash
# Package the already-checked release ROM; this script never builds it.
set +x
set -euo pipefail
umask 077

test -n "${BASEROM_URL:-}"
[[ "$PATCH_COMMIT" =~ ^[0-9a-f]{40}$ ]]
test "$(git rev-parse HEAD)" = "$PATCH_COMMIT"
release=build/expansion-modern-all-locales-all-features/release/aapcs
test -f "$release/fireemblem8.gba"
test -f "$release/generated/expansion_build_metadata.json"
test ! -e "$PATCH_ARTIFACT_DIR"

private_dir="$(python3 -c 'import sys,tempfile; print(tempfile.mkdtemp(prefix="patch-private.", dir=sys.argv[1]))' "$RUNNER_TEMP")"
base_image="$private_dir/base.gba"
cleanup() {
    status="$?"
    trap - EXIT
    if ! rm -f -- "$base_image" 2>/dev/null ||
       ! rmdir -- "$private_dir" 2>/dev/null; then
        echo "private patch input cleanup failed" >&2
        status=1
    fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if ! curl --fail --silent --location --proto '=https' --proto-redir '=https' \
    --tlsv1.2 --output "$base_image" --url "$BASEROM_URL" >/dev/null 2>&1; then
    echo "private patch input download failed" >&2
    exit 1
fi
unset BASEROM_URL
chmod 0400 "$base_image"
python3 -m scripts.modernize.patch_release create \
    --base "$base_image" --target "$release/fireemblem8.gba" \
    --metadata "$release/generated/expansion_build_metadata.json" \
    --output-dir "$PATCH_ARTIFACT_DIR" --commit "$PATCH_COMMIT"
python3 -m scripts.modernize.patch_release verify \
    --base "$base_image" --artifact-dir "$PATCH_ARTIFACT_DIR"
