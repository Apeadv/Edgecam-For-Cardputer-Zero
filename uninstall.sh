#!/bin/sh
set -e
sudo apt remove -y edgecam
systemctl --user restart APPLaunch
echo "EDGECAM removed"
