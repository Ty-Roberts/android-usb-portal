#!/bin/sh
# Package verified build outputs. No publication or automatic updates.
set -eu
cd "$(dirname "$0")/.."
VERSION=$(sed -n 's/^name=//p' version.properties)
ARCH=$(uname -m)
APP="$PWD/dist/Pocket Dimension.app"
APK="${ANDROID_APK:-$PWD/app/build/outputs/apk/debug/app-debug.apk}"
STAGE="$PWD/build/dmg-stage"
DMG="$PWD/dist/PocketDimension-$VERSION-$ARCH.dmg"
python3 - "$APP" "$VERSION" <<'PY'
import plistlib, sys
from pathlib import Path
info = plistlib.loads((Path(sys.argv[1])/'Contents/Info.plist').read_bytes())
assert info['CFBundleShortVersionString'] == sys.argv[2], 'Rebuild the Mac app for this version'
PY
codesign --verify --deep --strict "$APP"
SDK="${ANDROID_HOME:-$HOME/Library/Android/sdk}"
"$SDK/build-tools/36.0.0/apksigner" verify "$APK"
"$SDK/build-tools/36.0.0/aapt2" dump badging "$APK" | head -1 | rg "versionName='$VERSION'"
rm -rf "$STAGE"
mkdir -p "$STAGE"
ditto "$APP" "$STAGE/Pocket Dimension.app"
ln -s /Applications "$STAGE/Applications"
cp "$APK" "$PWD/dist/PocketDimension-$VERSION-android.apk"
cp "$APK" "$STAGE/PocketDimension-$VERSION-android.apk"
cp docs/releases.md "$STAGE/Install and update.txt"
cp usb-android/build/outputs/aar/usb-android-release.aar "$PWD/dist/PocketDimension-$VERSION-usb-android.aar"
hdiutil create -volname "Pocket Dimension $VERSION" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
if [ -n "${CODESIGN_IDENTITY:-}" ] && [ "$CODESIGN_IDENTITY" != - ]; then
    codesign --force --timestamp --sign "$CODESIGN_IDENTITY" "$DMG"
    if [ -n "${NOTARY_PROFILE:-}" ]; then
        xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait
        xcrun stapler staple "$DMG"
        xcrun stapler validate "$DMG"
        spctl --assess --type open --context context:primary-signature --verbose "$DMG"
    fi
fi
python3 - "$DMG" "$PWD/dist/PocketDimension-$VERSION-android.apk" "$PWD/dist/PocketDimension-$VERSION-usb-android.aar" <<'PY'
import hashlib, json, os, subprocess, sys
from pathlib import Path
files = [Path(p) for p in sys.argv[1:]]
manifest = {'version': dict(line.split('=',1) for line in Path('version.properties').read_text().splitlines())['name'],
            'architecture': subprocess.check_output(['uname','-m'], text=True).strip(),
            'mac_signing': 'Developer ID' if os.environ.get('CODESIGN_IDENTITY', '-') != '-' else 'ad-hoc local build',
            'notarization_requested': bool(os.environ.get('NOTARY_PROFILE')),
            'files': [{'name': p.name, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'bytes': p.stat().st_size} for p in files]}
Path(str(files[0]) + '.json').write_text(json.dumps(manifest, indent=2) + '\n')
PY
rm -rf "$STAGE"
printf 'Packaged %s\n' "$DMG"
