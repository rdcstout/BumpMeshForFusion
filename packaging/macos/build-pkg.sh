#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
VERSION=${1:-0.1.0}
DIST_DIR="$PROJECT_DIR/dist"
BUILD_DIR="$PROJECT_DIR/build/macos"
PAYLOAD_DIR="$BUILD_DIR/payload"
STAGED_ADDIN="$PAYLOAD_DIR/private/tmp/com.extrusiontherapy.bumpmeshforfusion/BumpMeshForFusion"

rm -rf "$BUILD_DIR"
mkdir -p "$STAGED_ADDIN" "$DIST_DIR"

rsync -a \
  --exclude '.git' \
  --exclude '.github' \
  --exclude 'build' \
  --exclude 'dist' \
  --exclude 'packaging' \
  --exclude 'tests' \
  "$PROJECT_DIR/" "$STAGED_ADDIN/"

chmod +x "$STAGED_ADDIN/Uninstall BumpMesh for Fusion.command"
xattr -cr "$PAYLOAD_DIR"

pkgbuild \
  --root "$PAYLOAD_DIR" \
  --scripts "$SCRIPT_DIR/scripts" \
  --identifier "com.extrusiontherapy.bumpmeshforfusion" \
  --version "$VERSION" \
  --install-location / \
  "$DIST_DIR/BumpMeshForFusion-macOS.pkg"

echo "$DIST_DIR/BumpMeshForFusion-macOS.pkg"
