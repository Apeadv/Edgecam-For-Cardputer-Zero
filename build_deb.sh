#!/bin/sh
# Builds dist/edgecam_<VERSION>_all.deb from src/, assets/, app.env and VERSION.
# Run on Linux (or on the CardputerZero) with dpkg-deb installed.
set -e
cd "$(dirname "$0")"
eval "$(tr -d '\r' < app.env)"
V=$(tr -d ' \r\n' < VERSION)
ROOT=build/$APP_ID
SHARE=/usr/share/APPLaunch/share/$APP_ID

rm -rf build && mkdir -p "$ROOT/DEBIAN" dist

mkdir -p "$ROOT$SHARE"
install -m 644 src/edgecam.py "$ROOT$SHARE/edgecam.py"
install -m 644 assets/icon-256.png "$ROOT$SHARE/icon-256.png"

mkdir -p "$ROOT/usr/share/APPLaunch/bin"
cat > "$ROOT/usr/share/APPLaunch/bin/$APP_ID" <<LAUNCH
#!/bin/sh
exec /usr/bin/python3 $SHARE/edgecam.py "\$@"
LAUNCH
chmod 755 "$ROOT/usr/share/APPLaunch/bin/$APP_ID"

install -D -m 644 assets/icon.png "$ROOT/usr/share/APPLaunch/share/images/$APP_ID.png"

mkdir -p "$ROOT/usr/share/APPLaunch/applications"
cat > "$ROOT/usr/share/APPLaunch/applications/$APP_ID.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=$APP_NAME
Comment=$APP_DESC
Exec=/usr/share/APPLaunch/bin/$APP_ID
Terminal=false
Sysplause=true
Icon=share/images/$APP_ID.png
Categories=Utility;
StartupNotify=false
DESK

cat > "$ROOT/DEBIAN/control" <<CTRL
Package: $APP_ID
Version: $V
Architecture: all
Maintainer: $MAINTAINER
Depends: python3, python3-numpy, python3-pil, python3-evdev, python3-picamera2, ffmpeg
Section: misc
Priority: optional
Description: $APP_DESC
CTRL

dpkg-deb --root-owner-group --build "$ROOT" "dist/${APP_ID}_${V}_all.deb" >/dev/null
rm -rf build
echo "OK: dist/${APP_ID}_${V}_all.deb"
echo "Install: sudo apt install ./dist/${APP_ID}_${V}_all.deb && systemctl --user restart APPLaunch"
