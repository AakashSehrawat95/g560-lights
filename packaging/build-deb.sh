#!/bin/bash
# Build dist/g560-lights_<version>_all.deb from this checkout. No root needed.
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION=$(python3 -c 'import re; print(re.search(r"__version__ = \"(.+?)\"", open("g560_lights/__init__.py").read()).group(1))')
MAINTAINER=${MAINTAINER:-"G560 Lights <g560-lights@localhost>"}
PKG=g560-lights
ROOT=build/${PKG}_${VERSION}
OUT=dist/${PKG}_${VERSION}_all.deb

rm -rf "$ROOT" && mkdir -p "$ROOT"/{DEBIAN,usr/bin,usr/lib/python3/dist-packages,usr/share/applications,usr/share/icons/hicolor/scalable/apps,usr/share/doc/$PKG,lib/udev/rules.d}

cp -r g560_lights "$ROOT/usr/lib/python3/dist-packages/"
find "$ROOT" -name __pycache__ -prune -exec rm -rf {} +

cat > "$ROOT/usr/bin/$PKG" <<'PY'
#!/usr/bin/python3
import sys

from g560_lights.cli import main

sys.exit(main())
PY
chmod 755 "$ROOT/usr/bin/$PKG"

install -m 644 data/io.g560lights.App.desktop "$ROOT/usr/share/applications/"
install -m 644 data/g560-lights.svg "$ROOT/usr/share/icons/hicolor/scalable/apps/"
install -m 644 data/70-g560-lights.rules "$ROOT/lib/udev/rules.d/"
install -m 644 LICENSE "$ROOT/usr/share/doc/$PKG/copyright"
install -m 644 README.md "$ROOT/usr/share/doc/$PKG/README.md"

cat > "$ROOT/DEBIAN/control" <<CTRL
Package: $PKG
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Depends: python3 (>= 3.8), python3-gi, python3-gi-cairo, gir1.2-gtk-4.0, gir1.2-adw-1, pulseaudio-utils, caps
Installed-Size: $(du -sk "$ROOT" | cut -f1)
Maintainer: $MAINTAINER
Description: Lighting control for Logitech G560 speakers
 A small GTK app to control the lights of Logitech G560 gaming speakers on
 Linux: static colors (per light), breathe, spectrum, wave, gradient, flash,
 candle and music-reactive effects, with brightness and speed controls,
 plus a graphic equalizer with presets for the speakers' sound.
 Installs a udev rule so no root is needed.
CTRL

cat > "$ROOT/DEBIAN/postinst" <<'SH'
#!/bin/sh
set -e
if [ "$1" = "configure" ]; then
    py3compile -p g560-lights 2>/dev/null || true
    if command -v udevadm >/dev/null 2>&1; then
        udevadm control --reload-rules || true
        udevadm trigger --action=add --subsystem-match=hidraw || true
    fi
    command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q /usr/share/applications || true
fi
exit 0
SH
cat > "$ROOT/DEBIAN/prerm" <<'SH'
#!/bin/sh
set -e
py3clean -p g560-lights 2>/dev/null || true
exit 0
SH
cat > "$ROOT/DEBIAN/postrm" <<'SH'
#!/bin/sh
set -e
if [ "$1" = "remove" ] || [ "$1" = "purge" ]; then
    command -v udevadm >/dev/null 2>&1 && udevadm control --reload-rules || true
    command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
fi
exit 0
SH
chmod 755 "$ROOT"/DEBIAN/{postinst,prerm,postrm}
# Debian standard modes regardless of the builder's umask
find "$ROOT" -type d -exec chmod 755 {} +
find "$ROOT" -type f ! -perm /111 -exec chmod 644 {} +

mkdir -p dist
fakeroot dpkg-deb --build "$ROOT" "$OUT" >/dev/null
echo "Built $OUT"
