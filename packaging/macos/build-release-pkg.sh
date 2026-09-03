#!/bin/zsh
set -euo pipefail

script_dir="${0:A:h}"
project_dir="${script_dir:h:h}"
version="${1:-0.1.1}"
notary_profile="${BUMPMESH_NOTARY_PROFILE:-spooly-notary}"
installer_identity="${BUMPMESH_INSTALLER_IDENTITY:-Developer ID Installer: Roger Stout (W3WPVL2V32)}"
package_path="$project_dir/dist/BumpMeshForFusion-macOS.pkg"

BUMPMESH_INSTALLER_IDENTITY="$installer_identity" \
  "$script_dir/build-pkg.sh" "$version"

pkgutil --check-signature "$package_path"
xcrun notarytool submit "$package_path" \
  --keychain-profile "$notary_profile" \
  --wait
xcrun stapler staple "$package_path"
xcrun stapler validate "$package_path"
spctl --assess --type install --verbose=4 "$package_path"

print "Signed, notarized, stapled, and verified: $package_path"
