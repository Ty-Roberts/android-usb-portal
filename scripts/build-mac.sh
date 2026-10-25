#!/bin/sh
# Run on the target Mac architecture. Python and libusb are embedded in the output.
set -eu
cd "$(dirname "$0")/.."
if [ "$(uname -s)" != Darwin ]; then
    printf 'This build script requires macOS.\n' >&2
    exit 1
fi
: "${PYTHON:=.venv/bin/python}"
if [ -z "${LIBUSB_DYLIB:-}" ]; then
    LIBUSB_DYLIB="$(brew --prefix libusb)/lib/libusb-1.0.0.dylib"
fi
LIBUSB_LICENSE="$(dirname "$(dirname "$LIBUSB_DYLIB")")/COPYING"
if [ ! -f "$LIBUSB_DYLIB" ] || [ ! -f "$LIBUSB_LICENSE" ]; then
    printf 'libusb or its COPYING license is missing. Run: brew install libusb\n' >&2
    exit 1
fi
mkdir -p build/mac dist
export PYINSTALLER_CONFIG_DIR="$PWD/build/mac/pyinstaller-cache"
set --
if [ -n "${CODESIGN_IDENTITY:-}" ] && [ "$CODESIGN_IDENTITY" != - ]; then set -- --codesign-identity "$CODESIGN_IDENTITY"; fi
"$PYTHON" -m PyInstaller "$@" --noconfirm --onefile --name pocket-dimension \
    --paths "$PWD/mac" --collect-all usb --collect-all keyring --recursive-copy-metadata cryptography --recursive-copy-metadata keyring \
    --add-binary "$LIBUSB_DYLIB:." \
    --add-data "$LIBUSB_LICENSE:licenses" \
    --add-data "$PWD/docs/third-party.txt:licenses" \
    --specpath build/mac --workpath build/mac/work --distpath dist mac/entrypoint.py
# The GUI uses a pre-extracted runtime to avoid unpacking Python on every click.
"$PYTHON" -m PyInstaller "$@" --noconfirm --onedir --name pocket-dimension-gui \
    --paths "$PWD/mac" --collect-all usb --collect-all keyring --recursive-copy-metadata cryptography --recursive-copy-metadata keyring \
    --add-binary "$LIBUSB_DYLIB:." \
    --add-data "$LIBUSB_LICENSE:licenses" \
    --add-data "$PWD/docs/third-party.txt:licenses" \
    --specpath build/mac --workpath build/mac/work-gui --distpath dist mac/entrypoint.py
ln -sf pocket-dimension dist/pocketpull
./dist/pocket-dimension --help
printf '\nBuilt %s/dist/pocket-dimension (%s)\n' "$PWD" "$(uname -m)"
