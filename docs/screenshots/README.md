# README screenshots

- `desktop.png`: the actual SwiftUI desktop view, rendered with synthetic sample folders and an inert helper through `tests/gui/DocumentationPreview.swift`. No USB connection, Keychain access or personal transfer history is used. Launch with `./scripts/preview-docs.sh`, then capture the preview window.
- `android.png`: the installed Android app's main screen, captured with `android screen capture -o docs/screenshots/android.png`. No file scope is shared in this image.

Inspect screenshots before committing: exclude personal files, account names, real device identifiers and certificate fingerprints. Pairing dialogs use screenshot protection and should remain protected in production builds.
