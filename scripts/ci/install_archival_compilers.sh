#!/usr/bin/env bash
set -euo pipefail

rm -rf .deps/agbcc-ci "$RUNNER_TEMP/mgfembp-agbcc-ci"
git init --quiet .deps/agbcc-ci
git -C .deps/agbcc-ci remote add origin https://github.com/pret/agbcc.git
git -C .deps/agbcc-ci fetch --quiet --depth=1 origin "$AGBCC_COMMIT"
git -C .deps/agbcc-ci checkout --quiet --detach FETCH_HEAD
test "$(git -C .deps/agbcc-ci rev-parse HEAD)" = "$AGBCC_COMMIT"
(
    cd .deps/agbcc-ci
    ./build.sh
    ./install.sh "$GITHUB_WORKSPACE"
)

git init --quiet "$RUNNER_TEMP/mgfembp-agbcc-ci"
git -C "$RUNNER_TEMP/mgfembp-agbcc-ci" remote add origin https://github.com/StanHash/agbcc.git
git -C "$RUNNER_TEMP/mgfembp-agbcc-ci" fetch --quiet --depth=1 origin "$MGFEMBP_AGBCC_COMMIT"
git -C "$RUNNER_TEMP/mgfembp-agbcc-ci" checkout --quiet --detach FETCH_HEAD
test "$(git -C "$RUNNER_TEMP/mgfembp-agbcc-ci" rev-parse HEAD)" = "$MGFEMBP_AGBCC_COMMIT"
(
    cd "$RUNNER_TEMP/mgfembp-agbcc-ci"
    ./build.sh
    ./install.sh "$GITHUB_WORKSPACE/mgfembp"
)
