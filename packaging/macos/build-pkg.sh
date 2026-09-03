#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
MANIFEST_VERSION=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$PROJECT_DIR/BumpMeshForFusion.manifest")
VERSION=${1:-$MANIFEST_VERSION}
[ "$VERSION" = "$MANIFEST_VERSION" ] || { echo "Installer version must match the add-in manifest." >&2; exit 1; }
DIST_DIR="$PROJECT_DIR/dist"
BUILD_DIR="$PROJECT_DIR/build/macos"
PAYLOAD_DIR="$BUILD_DIR/payload"
STAGED_ADDIN="$PAYLOAD_DIR/private/tmp/com.extrusiontherapy.bumpmeshforfusion/BumpMeshForFusion"
SIGNING_IDENTITY=${BUMPMESH_INSTALLER_IDENTITY:-}

rm -rf "$BUILD_DIR"
mkdir -p "$STAGED_ADDIN" "$DIST_DIR"

rsync -a \
  --exclude '.git' \
  --exclude '.github' \
  --exclude 'build' \
  --exclude 'dist' \
  --exclude 'packaging' \
  --exclude 'tests' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '.DS_Store' \
  "$PROJECT_DIR/" "$STAGED_ADDIN/"

chmod +x "$STAGED_ADDIN/Uninstall BumpMesh for Fusion.command"
xattr -cr "$PAYLOAD_DIR"

if [ -n "$SIGNING_IDENTITY" ]; then
  pkgbuild \
    --root "$PAYLOAD_DIR" \
    --scripts "$SCRIPT_DIR/scripts" \
    --identifier "com.extrusiontherapy.bumpmeshforfusion" \
    --version "$VERSION" \
    --install-location / \
    --sign "$SIGNING_IDENTITY" \
    "$DIST_DIR/BumpMeshForFusion-macOS.pkg"
else
  pkgbuild \
    --root "$PAYLOAD_DIR" \
    --scripts "$SCRIPT_DIR/scripts" \
    --identifier "com.extrusiontherapy.bumpmeshforfusion" \
    --version "$VERSION" \
    --install-location / \
    "$DIST_DIR/BumpMeshForFusion-macOS.pkg"
fi

echo "$DIST_DIR/BumpMeshForFusion-macOS.pkg"
