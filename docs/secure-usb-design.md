# Secure USB design

The reusable :usb-android AAR owns TLS, Android Keystore identity, host certificate validation, first-pair approval protocol, persistent trust records, fixed seven-day deadlines, active-session expiry, and manual revocation. It declares no storage, network, or broad device permissions. Consuming apps choose their business protocol and data scope and provide an approval callback; no app receives authorized application streams until library authorization completes.

The UI compares full SHA-256 certificate fingerprints on the phone and desktop, following the explicit key-approval model. This is not a short unauthenticated numeric-code comparison and is not a custom password encryption scheme. Both fingerprints must be checked on the physical screens. Phone approval alone never changes file-sharing scope.

TLS uses the Android platform JSSE/Conscrypt SSLEngine and Python's OpenSSL-backed ssl.MemoryBIO. Prefer TLS 1.3 where available; Android 8/9 interoperability can use TLS 1.2 with ECDHE-ECDSA and AEAD suites only. Both peers prove certificate private-key possession. Self-signed certificates are accepted during the bounded bootstrap phase only; the library's authorization gate and explicit fingerprint comparison establish trust. Normal file traffic has no plaintext downgrade.

Android identity keys remain in Android Keystore. The desktop identity is protected at rest in macOS Keychain using an explicitly selected macOS backend; no plaintext fallback. Desktop TLS loading may require a mode-0600 transient key file, deleted immediately after loading into the TLS context. Desktop process compromise remains outside transport encryption's protection.

Trust expires seven days after explicit approval and does not slide on reconnect. The phone enforces expiry and revocation on reads/writes and active sessions. Wall-clock expiry plus a monotonic deadline bounds active sessions; observed clock rollback invalidates trust. Offline expiry ultimately relies on the device's clock integrity; this does not defend a compromised OS or privileged time tampering. Work deployments requiring stronger guarantees should use managed certificates and trusted enterprise time.

A TLS-protected control exchange handles inspect/connect/pair/revoke before returning application streams. Pairing requires explicit phone approval and client confirmation on that same TLS session. Approval requests and handshakes have bounded deadlines. Closing a logical TLS session returns the accessory to its public greeting state; device discovery is public metadata only. Locking/detaching clears Pocket Dimension's active sharing selection; pairing records expire independently.

Live checks passed on the Samsung SM-S918U1: pairing persistence, encrypted file roundtrips, cancellation/resume, checksum readback, manual revocation and refusal after revocation. Temporary phone files were removed.

The automated interoperability suite runs the actual Android library control and TLS classes on the JVM against the Python client. This is not an independent security audit; Android 8/9 platform interoperability still needs a physical-device check.

References: https://developer.android.com/reference/javax/net/ssl/SSLEngine ; https://source.android.com/docs/core/ota/modular-system/conscrypt ; https://developer.android.com/privacy-and-security/keystore ; https://docs.python.org/3/library/ssl.html#memory-bio-support ; https://keyring.readthedocs.io/en/latest/ .

Platform integration note: Android Keystore authorizes `DIGEST_NONE` in addition to SHA-256/384/512 because the TLS provider hashes its handshake before requesting ECDSA signing. This does not enable unhashed TLS signatures; the negotiated TLS signature algorithm still supplies the digest. The physical regression check verifies prehashed signing and that the private key remains non-exportable. See [KeyGenParameterSpec.Builder.setDigests](https://developer.android.com/reference/android/security/keystore/KeyGenParameterSpec.Builder#setDigests(java.lang.String...)).
