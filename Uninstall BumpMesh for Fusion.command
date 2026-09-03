#!/bin/sh
set -eu

ADDIN_DIR="$HOME/Library/Application Support/Autodesk/FusionAddins/BumpMeshForFusion"
LEGACY_DIR="$HOME/Library/Application Support/Autodesk/Autodesk Fusion 360/API/AddIns/BumpMeshForFusion"

rm -rf "$ADDIN_DIR" "$LEGACY_DIR"
echo "BumpMesh for Fusion has been removed. Restart Fusion if it is open."
printf "Press Return to close this window."
read -r _answer

