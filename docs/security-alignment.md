# Best Practices and Security Alignment Update: authenticated USB access

**Priority:** High. The previous physical-USB boundary did not authenticate a computer or encrypt transferred files. This update separates discovery, cryptographic proof, computer approval and user-selected data scope.

## Implementation

The reusable `:usb-android` library owns Android Keystore identity, platform mutual TLS, fingerprint approval, private trust persistence, fixed seven-day expiry, manual revocation and active-stream authorization. It returns application streams only after its authorization gate. Pending pairing attempts are cancelled on lifecycle invalidation, shutdown or revocation; failed persistence cannot publish a new approval. Active sessions check both wall and monotonic deadlines. Observed clock rollback clears trust.

Pocket Dimension supplies foreground, unlocked approval UI displaying both full fingerprints, obscured-touch filtering and screenshot protection. USB attach or an incoming Intent cannot approve computer trust or choose files. Scope checks remain in each file operation. The manifest has no exported service/provider/receiver or network permission. The launcher/accessory activity remains exported for its intended system entry points; incoming Intents are not forwarded, USB devices are enumerated through UsbManager, permission PendingIntents are explicit and immutable, and onNewIntent updates the stored Intent.

The Mac client explicitly pins the phone certificate, stores its identity and phone pins in macOS Keychain, refuses plaintext downgrade, and uses no plaintext credential-storage fallback. Keychain resolution precedes USB TLS upgrade so an OS access prompt cannot leave the phone waiting mid-handshake. Discovery reveals model and app ID only. Error details are logged locally while the phone's session-failure UI remains generic.

Android's TLS provider requests prehashed ECDSA signatures; the Keystore key policy therefore authorizes DIGEST_NONE as well as SHA-256/384/512. TLS still supplies the negotiated digest. This integration is covered by a physical-device regression test. See [Android's setDigests documentation](https://developer.android.com/reference/android/security/keystore/KeyGenParameterSpec.Builder#setDigests(java.lang.String...)).

## Scope

- New library: `SecureAccessory`, `TrustPolicy`, `AndroidIdentity`, `TlsIdentity`, `TlsStream`, module Gradle configuration, empty manifest, consumer rules and integration README.
- Android app: `MainActivity.java`, `Wire.java`, `strings.xml`, app/root Gradle configuration and settings; instrumentation `StorageChecks.java`.
- Desktop: `security.py`, `protocol.py`, `discovery.py`, `cli.py`, native `PocketDimension.swift`, Python dependencies and bundle metadata collection.
- Verification: Java `SecurityHarness`, Python `test_secure_usb`, explicit legacy-only fixtures in prior protocol tests, GUI pairing checks, physical USB acceptance scripts and benchmark cleanup.
- Delivery/docs: build/release scripts, version properties, README, protocol/security design, third-party notices and todo.txt.

## Implementation diff

[Unified patch](security-alignment.patch) contains the exact new security implementation and follow-up changes for which before-images were retained. This workspace has no Git baseline; the already-integrated desktop/application changes are described above rather than reconstructing an unreliable historical diff.

## Validation and limits

107 automated client/interoperability tests and native GUI checks pass. JVM interoperability executes the actual library TLS and authorization classes against Python, including TLS 1.3, TLS 1.2 AEAD, fragmentation, tampering, missing certificates, denial, expiry, revocation, pending-pair cancellation and transport reuse. Seven instrumentation checks pass on the connected Samsung phone, including non-exportable Keystore identity persistence and TLS prehashed signing. Physical encrypted-transfer checks passed (12 MiB files), including cancellation, resume, stored-file readback, repeated secure sessions and overwrite/wrong-phone refusals. Generated files were removed. Results: `build/security-phone-acceptance.json`. Manual unpair succeeded; the next connection returned pairing_required.

This is not an independent security audit. Android 8/9 platform interoperability remains untested on a device. Offline seven-day expiry relies on device clock integrity; privileged OS compromise is outside this boundary. Consuming apps must exclude trust preferences from backup, handle lifecycle invalidation, enforce their own data scope and recheck authorization during long local operations. Ad-hoc desktop builds may need fresh macOS Keychain access approval after rebuilding; public signing is tracked separately.
