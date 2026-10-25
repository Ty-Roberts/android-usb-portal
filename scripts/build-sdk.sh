#!/bin/sh
# Direct SDK build for restricted environments where Gradle's local sockets are unavailable.
set -eu
cd "$(dirname "$0")/.."
: "${ANDROID_HOME:=${ANDROID_SDK_ROOT:-$HOME/Library/Android/sdk}}"
: "${JAVA_HOME:=/Applications/Android Studio.app/Contents/jbr/Contents/Home}"
export JAVA_HOME
BUILD_TOOLS="$ANDROID_HOME/build-tools/36.0.0"
ANDROID_JAR="$ANDROID_HOME/platforms/android-36/android.jar"
OUT="$PWD/app/build/sdk-debug"
mkdir -p "$OUT/classes" "$OUT/dex" "$OUT/generated"
python3 - "$OUT" <<'PY'
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
ET.register_namespace('android', 'http://schemas.android.com/apk/res/android')
manifest = ET.parse('app/src/main/AndroidManifest.xml')
root = manifest.getroot()
root.set('package', 'dev.pocketpull')
version = dict(line.split('=', 1) for line in Path('version.properties').read_text().splitlines() if '=' in line)
root.set('{http://schemas.android.com/apk/res/android}versionCode', version['android'])
root.set('{http://schemas.android.com/apk/res/android}versionName', version['name'])
root.find('application').set('{http://schemas.android.com/apk/res/android}debuggable', 'true')
ET.SubElement(root, 'uses-sdk', {'{http://schemas.android.com/apk/res/android}minSdkVersion': '26',
                              '{http://schemas.android.com/apk/res/android}targetSdkVersion': '36'})
manifest.write(Path(sys.argv[1]) / 'AndroidManifest.xml')
PY
"$BUILD_TOOLS/aapt2" compile --dir app/src/main/res -o "$OUT/resources.zip"
"$BUILD_TOOLS/aapt2" link -o "$OUT/resources.apk" -I "$ANDROID_JAR" --java "$OUT/generated" --manifest "$OUT/AndroidManifest.xml" "$OUT/resources.zip"
"$JAVA_HOME/bin/javac" -source 17 -target 17 -classpath "$ANDROID_JAR" -d "$OUT/classes" app/src/main/java/dev/pocketpull/*.java usb-android/src/main/java/dev/pocketdimension/usb/*.java "$OUT/generated/dev/pocketpull/R.java"
"$JAVA_HOME/bin/jar" cf "$OUT/classes.jar" -C "$OUT/classes" .
"$BUILD_TOOLS/d8" --min-api 26 --lib "$ANDROID_JAR" --output "$OUT/dex" "$OUT/classes.jar"
cp "$OUT/resources.apk" "$OUT/unsigned.apk"
(cd "$OUT/dex" && zip -q -u "$OUT/unsigned.apk" classes.dex)
"$BUILD_TOOLS/zipalign" -f -p 4 "$OUT/unsigned.apk" "$OUT/aligned.apk"
if [ ! -f "$OUT/debug.keystore" ]; then
    "$JAVA_HOME/bin/keytool" -genkeypair -keystore "$OUT/debug.keystore" -storepass android -keypass android -alias androiddebugkey -dname 'CN=Android Debug,O=Android,C=US' -keyalg RSA -keysize 2048 -validity 3650
fi
"$BUILD_TOOLS/apksigner" sign --ks "$OUT/debug.keystore" --ks-pass pass:android --out "$OUT/pocket-dimension-debug.apk" "$OUT/aligned.apk"
"$BUILD_TOOLS/apksigner" verify "$OUT/pocket-dimension-debug.apk"
ln -sf pocket-dimension-debug.apk "$OUT/pocketpull-debug.apk"
printf 'Built %s\n' "$OUT/pocket-dimension-debug.apk"
