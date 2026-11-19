#!/bin/sh
# Install / update EDGECAM from GitHub, directly on the CardputerZero:
#   curl -fsSL https://raw.githubusercontent.com/YOUR_GITHUB_LOGIN/edgecam/main/get.sh | sh
set -e
REPO="YOUR_GITHUB_LOGIN/edgecam"   # <-- change to your public repository
BRANCH="main"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
echo "Downloading $REPO ($BRANCH)..."
curl -fsSL "https://github.com/$REPO/archive/refs/heads/$BRANCH.tar.gz" \
  | tar xz -C "$TMP" --strip-components=1
cd "$TMP"
sh build_deb.sh
sudo apt install -y --reinstall ./dist/*_all.deb
systemctl --user restart APPLaunch
echo "Installed. Look for EDGECAM in the launcher."
