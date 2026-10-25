#!/bin/sh
set -eu
TIMESTAMP=--timestamp=none
if [ "${CODESIGN_IDENTITY:--}" != - ]; then TIMESTAMP=--timestamp; fi
cd "$(dirname "$0")/.."
if [ ! -x dist/pocket-dimension-gui/pocket-dimension-gui ]; then
    printf 'Build the CLI first: ./scripts/build-mac.sh\n' >&2
    exit 1
fi
APP="$PWD/dist/Pocket Dimension.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" build/swift-cache
xcrun swiftc -swift-version 5 -parse-as-library -O -target "$(uname -m)-apple-macosx13.0" \
    -module-cache-path "$PWD/build/swift-cache" -framework SwiftUI -framework AppKit -framework UniformTypeIdentifiers \
    mac/gui/PocketDimension.swift -o "$APP/Contents/MacOS/PocketDimension"
rm -f "$APP/Contents/Resources/pocket-dimension"
rm -rf "$APP/Contents/Resources/cli"
ditto dist/pocket-dimension-gui "$APP/Contents/Resources/cli"
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleExecutable</key><string>PocketDimension</string>
<key>CFBundleIdentifier</key><string>dev.pocketpull.mac</string>
<key>CFBundleName</key><string>Pocket Dimension</string>
<key>CFBundleDisplayName</key><string>Pocket Dimension</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>0.5.0</string>
<key>CFBundleVersion</key><string>7</string>
<key>LSMinimumSystemVersion</key><string>13.0</string>
<key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
cp mac/assets/PocketDimension.icns "$APP/Contents/Resources/"
python3 - "$APP" <<'PYINFO'
from pathlib import Path
import plistlib, sys
version = dict(line.split('=', 1) for line in Path('version.properties').read_text().splitlines() if '=' in line)
file = Path(sys.argv[1])/'Contents/Info.plist'
info = plistlib.loads(file.read_bytes())
info.update(CFBundleShortVersionString=version['name'], CFBundleVersion=version['mac'], CFBundleIconFile='PocketDimension.icns')
file.write_bytes(plistlib.dumps(info))
PYINFO
# Remove obsolete extension artifacts when rebuilding an existing app bundle.
rm -rf "$APP/Contents/PlugIns"
codesign --force --options runtime "$TIMESTAMP" --sign "${CODESIGN_IDENTITY:--}" "$APP"
codesign --verify --deep --strict "$APP"
printf 'Built %s\n' "$APP"
