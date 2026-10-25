#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
mkdir -p build/security-tests
if [ ! -f build/security-tests/json.jar ]; then
    curl -fsSL https://repo.maven.apache.org/maven2/org/json/json/20250517/json-20250517.jar -o build/security-tests/json.jar
fi
PYTHONPATH=mac .venv/bin/python -m unittest discover -s tests -p 'test_secure_usb.py' -v
