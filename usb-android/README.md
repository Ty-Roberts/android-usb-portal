# Secure USB Android library

`:usb-android` builds a reusable AAR with no runtime dependencies or manifest permissions. It owns the Keystore identity, mutual TLS bootstrap, fingerprint pairing, private trust records, fixed seven-day expiry, manual revocation, and authorization of application streams. Pocket Dimension's file browser and storage permissions stay in `:app`.

Build with `./gradlew :usb-android:assembleRelease`. The artifact is `usb-android/build/outputs/aar/usb-android-release.aar`. Add the module with `implementation(project(":usb-android"))`, or distribute the AAR through your organization's Maven repository. Minimum Android API is 26; Java 17 is used for building. Platform TLS 1.3 is preferred, with TLS 1.2 ECDHE-ECDSA AES-GCM available on older platforms. Android 8/9 interoperability has not yet been device-tested.

## Integration

Create one owner per application security namespace, using the application context. Construction, approval waits, TLS and stream operations run on a dedicated I/O worker, never the main thread:

```java
SecureAccessory security = new SecureAccessory(
    context.getApplicationContext(), "com.example.work.usb",
    request -> {
        // Post your approval UI to the main thread; wait here on the I/O worker.
        // Show request.computerName and BOTH full fingerprints:
        // request.computerFingerprint and request.phoneFingerprint.
        // Require comparison with the desktop; deny on timeout, cancel or lock.
        return approvalUi.awaitExplicitDecision(request);
    });

SecureAccessory.Session session = security.accept(records);
if (session != null) {
    try (SecureAccessory.Session authorized = session) {
        // Only these streams may carry your business protocol.
        serveYourProtocol(authorized.input, authorized.output);
        // During long local work, also call authorized.requireAuthorized().
    }
}
```

`approvalUi` and `serveYourProtocol` are host-app callbacks, not library classes. The app never writes trust records itself. `accept()` returns `null` after inspection, denial or revocation; errors must fail closed. Keep the owner alive across logical connections; call `close()` when permanently shutting it down.

Implement `TlsStream.Records` over your accessory transport. `receive()` must return one nonempty ciphertext fragment of at most 16,379 bytes; `send()` preserves fragment boundaries; `abort()` must promptly close the underlying descriptor and unblock pending I/O. One worker owns each TLS engine; revocation may abort it from another thread. Pocket Dimension wraps fragments in type-13 frames and uses a separate public greeting/upgrade phase; that small framing adapter lives in `MainActivity.serve()`. For another app, implement the same framing on the desktop or use your own matching record adapter. The AAR does not select, open, or claim USB accessories on behalf of the app.

Never expose the raw accessory streams to business handlers. Reject commands before `accept()` returns an authorized session. `TlsStream` is a transport primitive and alone does not authorize a peer. The complete desktop handshake and control format are documented in [protocol.md](../docs/protocol.md); the reference Python client is `mac/pocketpull/security.py`.

## Lifecycle and trust management

- `trustedComputers()` returns the library-owned name, fingerprint, approval and expiry times. Show these in your app's settings.
- `revoke(fingerprint)` and `revokeAll()` persist removal and terminate matching active sessions. Surface persistence errors; do not report permanent removal if storage failed.
- `invalidateSessions()` aborts active streams and pending pairing attempts while preserving unexpired trust. Call it on screen off, USB detach, app shutdown, or your own data-policy revocation. Your app must clear its own data scope at these boundaries.
- The library independently checks keyguard state before access and each guarded read/write. Your app should additionally require foreground, unlocked, unobscured approval UI, and cancel pending decisions on lifecycle changes. Pocket Dimension demonstrates `FLAG_SECURE` and obscured-touch filtering.
- Expiry is seven days after explicit approval, with no reconnect renewal. Wall time and monotonic active deadlines are enforced. Observed wall-clock rollback clears trust. Offline clock tampering by a compromised OS is outside this guarantee.
- Exclude `<namespace>.trust.xml` preferences from cloud backup and device-transfer restore. Keystore keys are not backed up; a changed local certificate invalidates restored trust automatically. Choose a stable, app-specific namespace. Apps sharing a UID must coordinate namespaces and lifecycle.

File access is a separate app policy: authenticate a computer, then authorize every business operation against the user-selected scope. Never interpret a paired computer as broad storage consent. Long-running local operations must recheck authorization before reading or committing data, because stream checks only surround I/O.

The library does not provide enterprise identity enrollment, attestation, remote administration or secure time. Test your app's lifecycle, scopes and platform versions, and obtain an independent security review before a work deployment. Test entry point: `./scripts/test-security.sh` (actual Java library with Python TLS peer, plus trust-policy checks).
