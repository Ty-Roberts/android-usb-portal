#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
mkdir -p build/swift-cache
xcrun swiftc -module-cache-path "$PWD/build/swift-cache" mac/assets/AppIcon.swift -o build/draw-icon
./build/draw-icon build/PocketDimension.iconset
iconutil -c icns build/PocketDimension.iconset -o mac/assets/PocketDimension.icns
