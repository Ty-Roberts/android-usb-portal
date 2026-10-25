#!/bin/sh
# Render the actual desktop GUI with synthetic documentation data, never real USB.
set -eu
cd "$(dirname "$0")/.."
APP="$PWD/build/docs-preview/Pocket Dimension Preview.app"
mkdir -p "$APP/Contents/MacOS" build/swift-cache
cat > build/docs-preview/sample-cli <<'CLI'
#!/bin/sh
case "$1" in
    devices) printf '[{"vid":6353,"pid":11521,"bus":1,"address":1,"name":"Android phone"}]\n' ;;
    *) printf 'Documentation preview only. USB operations are disabled.\n' >&2; exit 1 ;;
esac
CLI
chmod +x build/docs-preview/sample-cli
xcrun swiftc -swift-version 5 -D GUI_TESTING -parse-as-library \
    -target "$(uname -m)-apple-macosx13.0" -module-cache-path "$PWD/build/swift-cache" \
    -framework SwiftUI -framework AppKit -framework UniformTypeIdentifiers \
    mac/gui/PocketDimension.swift tests/gui/DocumentationPreview.swift -o "$APP/Contents/MacOS/DocumentationPreview"
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleExecutable</key><string>DocumentationPreview</string>
<key>CFBundleIdentifier</key><string>dev.pocketdimension.docs-preview</string>
<key>CFBundleName</key><string>Pocket Dimension Preview</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>LSMinimumSystemVersion</key><string>13.0</string>
<key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
codesign --force --sign - "$APP"
open "$APP" --args "$PWD/build/docs-preview/sample-cli"
