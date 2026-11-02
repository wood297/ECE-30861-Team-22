#!/usr/bin/env bash
# Packs the current commit, plus a VERSION file, into a release tarball. Prints the version.
#
#   deploy/build_release.sh <output.tar.gz> [branch name]
set -euo pipefail

OUTPUT="${1:?usage: deploy/build_release.sh <output.tar.gz> [branch name]}"
BRANCH="${2:-$(git rev-parse --abbrev-ref HEAD)}"
VERSION="$BRANCH@$(git rev-parse --short HEAD)"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
git archive HEAD | tar -x -C "$STAGE"
echo "$VERSION" > "$STAGE/VERSION"
tar -czf "$OUTPUT" -C "$STAGE" .
echo "$VERSION"
