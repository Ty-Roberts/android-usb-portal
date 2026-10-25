#!/bin/sh
# Opt-in: phone must expose only StorageChecks -e usb true generated fixture.
set -eu
cd "$(dirname "$0")/.."
mkdir -p build/gui-tests build/swift-cache
xcrun swiftc -swift-version 5 -D GUI_TESTING -parse-as-library \
    -target "$(uname -m)-apple-macosx13.0" -module-cache-path "$PWD/build/swift-cache" \
    -framework SwiftUI -framework AppKit -framework UniformTypeIdentifiers \
    mac/gui/PocketDimension.swift tests/gui/PhoneChecks.swift -o build/gui-tests/phone-checks
./build/gui-tests/phone-checks "$PWD/dist/pocket-dimension"
