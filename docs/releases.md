Pocket Dimension 0.7.0 — local development build

Both apps must be updated together. File operations now require authenticated TLS. First pairing compares both full certificate fingerprints on the Mac and phone, followed by explicit phone approval. Trust expires seven days after approval and can be revoked in Android Trusted computers or the Mac Security menu. Locking/detaching clears the selected sharing scope.

The reusable Android security library is built separately as usb-android-release.aar; integration instructions are in usb-android/README.md. It owns pairing, encrypted streams and trust policy. Phone permission to open an accessory does not grant file access by itself.

The Mac build is ad-hoc signed and the APK uses the existing debug identity; public Developer ID signing/notarization still requires release credentials. This implementation has automated security tests and has passed live encrypted transfer, cancellation, resume, persisted-trust and revocation checks on the connected Samsung phone. It has not had an independent security audit.

Pocket Dimension install and update

This development build targets Apple Silicon Macs running macOS 13 or later and Android 8 or later. Drag Pocket Dimension.app to Applications. Install the enclosed Android APK on your phone, then open it and choose the folder to share. Keep the phone unlocked and connect a USB data cable. Click Connect on the Mac.

Local builds are ad-hoc signed, not Apple-notarized public releases. The included Android APK is debug signed. Use these for development; a public release needs stable signing identities. Existing files are never overwritten.

Updates are manual: quit the Mac app, replace it, and install a compatible newer APK without uninstalling the phone app. Queue history is saved under ~/Library/Application Support/PocketDimension/history.json. Interrupted jobs remain waiting for an explicit retry; the original phone and sharing scope must match. Resume data lives under ~/Library/Caches/PocketDimension/resume on the Mac and private app cache on Android. Android may evict cached data; resume safely restarts when no prefix remains.

Build from source with ./scripts/build-release.sh. version.properties controls Android and Mac versions; mac/pyproject.toml must match. The Gradle build uses the existing Android debug key. scripts/build-sdk.sh retains its separate legacy debug key; do not interchange APKs signed by different keys for an installed app. Check apksigner verify --print-certs before updating. Back up the chosen signing key privately. Never commit a keystore or its password.

For a distributable Mac release, set CODESIGN_IDENTITY to your Developer ID Application identity before building; PyInstaller signs the embedded runtime with that identity. Set NOTARY_PROFILE to an existing notarytool Keychain profile to submit and staple the resulting DMG. Do not re-sign just the PyInstaller wrapper with hardened runtime: its extracted libraries must have matching signatures. package-release.sh verifies signatures and versions and writes a SHA-256 manifest beside the DMG. No automatic update service is installed and nothing is published.

For Android production distribution, sign an unsigned release APK using your stable Android release key, then pass ANDROID_APK=/absolute/path/to/signed.apk to package-release.sh. Verify its version and certificate. An APK signed with a new key cannot replace the debug build without losing the existing installation identity; migrate intentionally rather than silently uninstalling.


The local v0.5 DMG was mounted read-only and verified: disk-image checksum, deep app code signature, app version, included APK, Applications symlink, and embedded CLI launch all passed. This verifies packaging integrity; it does not imply Apple notarization or Gatekeeper approval.

The GUI now embeds its helper under Contents/Resources/cli with the pre-extracted runtime. Keep that directory intact when copying or signing the app. build-mac.sh produces both the portable single-file CLI and the directory runtime used by build-gui.sh.
