#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
DIST_DIR="$PROJECT_DIR/dist"
BUILD_DIR="$PROJECT_DIR/build/manual"
STAGED_ADDIN="$BUILD_DIR/BumpMeshForFusion"

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
rm -f "$DIST_DIR/BumpMeshForFusion-Manual.zip"
(
  cd "$BUILD_DIR"
  zip -qry "$DIST_DIR/BumpMeshForFusion-Manual.zip" BumpMeshForFusion
)

echo "$DIST_DIR/BumpMeshForFusion-Manual.zip"
