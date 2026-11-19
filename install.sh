#!/bin/sh
# Local install from a release checkout (no GitHub round-trip).
set -e
cd "$(dirname "$0")"
DEB=$(ls -1 dist/edgecam_*.deb 2>/dev/null | tail -1)
if [ -z "$DEB" ]; then
  echo "No .deb in dist/, building first..."
  sh build_deb.sh
  DEB=$(ls -1 dist/edgecam_*.deb | tail -1)
fi
sudo apt install -y --reinstall "$DEB"
systemctl --user restart APPLaunch
echo "Installed $DEB"
