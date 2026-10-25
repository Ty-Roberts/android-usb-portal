#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
export JAVA_HOME="${JAVA_HOME:-/Applications/Android Studio.app/Contents/jbr/Contents/Home}"
PYTHONPATH=mac .venv/bin/python -m unittest discover -s tests
./scripts/test-gui.sh
./scripts/build-icon.sh
./scripts/build-mac.sh
./gradlew assembleDebug lintDebug :usb-android:assembleRelease :usb-android:lintRelease
./scripts/build-gui.sh
./scripts/package-release.sh
