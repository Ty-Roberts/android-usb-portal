# Pocket Dimension: detailed usage and development

Home of **Pocket Dimension**, the desktop and Android file-transfer apps, and a reusable Android library for secure USB accessory connections.

Move files and folders between an Android phone and a Mac over USB, without requiring ADB, USB debugging, or a network connection. Pocket Dimension uses Android Open Accessory (AOA); every file is verified with SHA-256.

## Start here

Install the latest APK on your phone and open Pocket Dimension. Connect an unlocked phone using a data cable. Close other Android file-transfer apps on the Mac.

Choose your sharing scope in Android:

- **Choose a file**: download that one file.
- **Choose a folder**: browse, recursively download, and upload inside that folder. The system picker grants access without broad storage permission.
- **Share device storage**: on Android 11+, opens Android's all-files-access settings. Enable Pocket Dimension, then return to share primary device storage. This is optional; the folder picker remains available.

Android restricts other apps' private files and some protected directories even with all-files access. Device-storage sharing exposes the primary shared-storage volume, not the entire OS filesystem or a root shell. See [Android's all-files access guide](https://developer.android.com/training/data-storage/manage-all-files).

**Stop sharing** revokes the session selection. Keep the app open during transfers. Closing/recreating the activity clears the sharing scope; Android's all-files permission remains a system setting until you turn it off. The app follows system dark mode.

## Native Mac GUI

Open `dist/Pocket Dimension.app`, or run:

```sh
./dist/pocket-dimension gui
```

1. Select your phone and click **Connect**. Approve the Android accessory prompt if needed. For first pairing, compare **both complete fingerprints** on the desktop and phone, then approve **Trust for 7 days** on the phone.
2. Choose a file, folder, or device storage on Android. Click **Connect** again after changing the phone selection.
3. Double-click anywhere on a folder row (or select it and press Return/click **Open folder**) to browse. Use Back/Forward, the up arrow, or the relative path field to navigate. ⌘[ and ⌘] go back/forward, ⌘↑ opens the enclosing folder, and ⌘R refreshes.
4. Use ⌘-click to select individual rows, Shift-click for a range, and ⌘A/**Select all** for visible items. **Download N selected…** downloads selected files and folders into one chosen parent directory. A single file still offers Save As. With no selection, download the current shared folder. The filter searches the current folder; filtered-out items are removed from selection.
5. **Upload files or folders…** accepts multiple local items and sends them to the current phone folder. Upload is enabled only for writable folder scopes. Right-click rows for Open folder and Download actions.

The window shows transfer progress, the current batch item, and checksum verification. Batches run sequentially and pause on the first error, retaining completed transfers. Choose **Stop**, **Skip**, or **Keep Both** in **If a name exists**; the GUI defaults to Keep Both. Stop preflights download destinations; Skip and Keep Both resolve conflicts without overwriting files. Existing files are never overwritten. Use the path field to navigate to a relative folder within the sharing scope. USB operations run in a background process so the window stays responsive. Only one operation runs at a time.

The Apple Silicon app is a local, ad-hoc-signed build for macOS 13+. It embeds the standalone CLI, Python, and libusb. It is not Apple-notarized.

## Terminal commands

The standalone executable requires neither a virtual environment nor an installed Python/libusb:

```sh
./dist/pocket-dimension connect
./dist/pocket-dimension ls
./dist/pocket-dimension ls --remote DCIM
./dist/pocket-dimension pull ~/Downloads/phone-folder
./dist/pocket-dimension pull ~/Downloads/photo.jpg --remote DCIM/Camera/photo.jpg
./dist/pocket-dimension push ./notes.txt
./dist/pocket-dimension push ./Photos
```

Paths passed to `--remote` are relative to the Android selection. A selected folder called `DCIM` already has DCIM as its root; use `Camera/photo.jpg`, not `DCIM/Camera/photo.jpg`, in that case. Without `--remote`, pull downloads the selected root and push uses the local source's basename. To choose an upload name/location, use `push ./notes.txt --remote Documents/new-notes.txt`; its parent must exist.

`connect`, `status`, and transfer commands discover recognized phones and start AOA when needed. Running `pocket-dimension` with no arguments also connects. `status` prints the current Android scope. A new Android APK is required for browsing, directories, and uploads; the client gives an upgrade message when connected to the original app.

## Computer trust and encryption

Version 0.7 requires authenticated TLS for file operations. Install both updated apps; older plaintext clients/APKs are refused. Discovery exposes phone model and app ID only. Android accessory permission and computer trust are separate approvals.

Trust lasts seven days from explicit phone approval; reconnecting does not extend it. Expiry or manual revocation ends active sessions. Use Android **Trusted computers** to forget one or all computers, or desktop **Security → Revoke this computer on phone**. Locking or detaching clears the current file selection; unlock and choose a scope again to continue. Stored trust alone never shares files.

For Terminal pairing, run `connect`, compare the printed phone and computer SHA-256 fingerprints with the phone approval screen, then run `pair --phone-fingerprint FULL_PHONE_FINGERPRINT`. Approve only if both match. `unpair` revokes this computer. macOS may separately ask permission for the desktop helper to access its Keychain identity; that is distinct from the phone approval. Desktop keys and phone pins are stored in macOS Keychain; phone identity keys use Android Keystore.

The reusable [Android library](../usb-android/README.md) owns this security boundary. See [design and limitations](secure-usb-design.md). This implementation has automated adversarial tests but has not had an independent security audit.

## Transfer queue and cancellation

Open **Transfers** to see session history with Waiting, Transferring, Completed, Skipped, Failed, and Cancelled states. **Reveal** opens a completed download in Finder, including its actual Keep Both filename.

- **Cancel queue** requests a safe stop of the current file and leaves later items waiting.
- **Skip item** cancels the current item safely, then advances to the next one. An unconfirmed cancellation/error pauses the queue.
- **Retry unfinished** retries failed/cancelled items and continues waiting ones; it never repeats completed/skipped rows.
- **Skip failed and continue** leaves failed items out and runs the remaining waiting work.
- **Clear completed** removes completed/skipped history rows. History is stored privately on the Mac and survives relaunch.

Install matching current builds for cancellation at USB batch boundaries, partial-upload cleanup and clean session reuse. Legacy plaintext APKs are refused by the current client. A slow provider or USB read can also delay the cancellation response; the UI stays in Cancelling until the operation finishes or times out. A file that finishes verification just before cancellation is kept.

In Terminal, Ctrl-C requests the same cooperative cancellation. Conflict policy is explicit:

```sh
./dist/pocket-dimension pull ~/Downloads/photo.jpg --remote Pictures/photo.jpg --conflict keep-both
./dist/pocket-dimension push ./Photos --conflict skip
```

The CLI default is `--conflict error`. For downloads, an existing directory is skipped as a whole or copied into a new sibling for Keep Both. For uploads, Skip merges folders and skips existing files; Keep Both creates a new sibling for a conflicting folder.

With the new APK, queued jobs record opaque phone/scope IDs. After unplug/replug, connect to the same phone and re-share the original folder before retrying. A different phone or sharing scope is rejected before data is transferred. Older APKs lack these identities, so an address-changing reconnect may require starting a new transfer. With Resume enabled, retries verify and reuse a private partial file; otherwise they start the unfinished file again.

Progress goes to stderr and includes bytes, percentage when size is known, and transfer rate. `--no-progress` silences it. `--json-progress` emits newline-delimited JSON progress on stderr; `--json` emits result JSON on stdout. `ls --json` and `devices --json` are also available for integrations.

Increase the USB inactivity timeout for slow file providers:

```sh
./dist/pocket-dimension pull --timeout 120 ~/Downloads/my-file.jpg
```

To call `pocket-dimension` from anywhere, install it into a directory already on your PATH, for example:

```sh
mkdir -p ~/.local/bin
install -m 755 ./dist/pocket-dimension ~/.local/bin/pocket-dimension
```

Keep `Pocket Dimension.app` beside the standalone CLI if using its `gui` launcher; otherwise open the app directly from Finder.

## Device discovery and manual fallback

```sh
./dist/pocket-dimension devices
./dist/pocket-dimension devices --all
```

The first lists recognized phones with names, IDs, bus, and address. The second includes other peripherals. The conservative table covers common Samsung Galaxy, Google Nexus/Pixel, OnePlus 6T, Xiaomi/Redmi, and AOA USB identities. IDs identify candidates, not proof of accessory support. Discovery does not send vendor probes to unrelated peripherals.

For multiple matching phones, use `--bus N --address N`. For an unrecognized phone, compare `devices --all` before/after unplugging it, then supply its IDs:

```sh
./dist/pocket-dimension connect --vid 0x04e8 --pid 0x6860
```

Those sample IDs are Samsung's common MTP identity; replace them with your phone's actual values. IDs/addresses change after an AOA switch, so omit stale IDs in later commands. Switching modes may interrupt other USB functions until unplugged.

To test only the AOA USB handshake, without requiring the Android app:

```sh
./dist/pocket-dimension probe --vid 0x04e8 --pid 0x6860 --switch
```

Already in AOA mode? Use `probe` with no IDs. A successful probe verifies interface 0's endpoints can be claimed; `connect` verifies app communication.

## Transfer guarantees and limits

- Downloads require an existing, writable parent directory. Existing destinations follow the chosen Stop, Skip, or Keep Both policy; no policy overwrites data. Individual files are saved from temporary output only after matching SHA-256 and byte count.
- A recursive download creates a new destination folder and verifies every file independently. A normal failure removes that newly created folder. It never merges into an existing local directory: Skip skips that folder, while Keep Both creates a new sibling.
- Uploads require a writable shared folder/device-storage scope. Android rejects existing filenames and verifies stored bytes by reading them back before confirming success.
- Upload files can be visible while receiving. A normal error cleans up the current incomplete file; forced termination/power loss or a provider failure can leave partial output.
- Recursive uploads can reuse directories, but never replace files. If a later file fails, earlier verified files and created directories remain on the phone.
- Symbolic links and path traversal are rejected. Directory depth/entry limits prevent unbounded traversal. Permission-denied locations cause the operation to fail rather than silently omit content.
- Clear sharing during an operation to revoke approval; checks occur between reads/frames. A confirmed cooperative cancellation leaves the session ready for reuse. After transport loss or an unconfirmed protocol error, reconnect before retrying. Enable Resume to verify and reuse retained private partial data.

SHA-256 verifies file integrity; authenticated TLS and explicit fingerprint pairing establish the computer identity. Share only the scope you intend to expose. Cloud-backed document-provider files can require network access to load; use local files for an offline workflow.

## Build Android

Install Android Studio with its bundled JDK and Android SDK platform 36/build tools 36.0.0. Set `ANDROID_HOME` or add untracked `local.properties` with `sdk.dir=/path/to/sdk`.

```sh
./gradlew :app:assembleDebug :app:lintDebug
```

APK: `app/build/outputs/apk/debug/app-debug.apk`. Android Studio can also build the repository. Copy the APK to your phone and use Android's installer; ADB is optional for development.

For restricted environments:

```sh
./scripts/build-sdk.sh
```

Output: `app/build/sdk-debug/pocket-dimension-debug.apk`. Set `JAVA_HOME`/`ANDROID_HOME` to override the macOS defaults. This fallback does not run lint. Its debug key is retained in `app/build/sdk-debug/debug.keystore`; Gradle uses a different key. To update an APK installed through the SDK fallback, use that same build route; changing signing keys may require uninstalling the old app.

## Build Mac CLI and GUI

For source use, Python 3.10+ and Homebrew libusb are required:

```sh
brew install libusb
python3 -m venv .venv
source .venv/bin/activate
python -m pip install './mac[bundle]'
./scripts/build-mac.sh
./scripts/build-gui.sh
```

Outputs: `dist/pocket-dimension` and `dist/Pocket Dimension.app`. GUI compilation requires Apple's command-line tools (`xcode-select --install`). Build on a Mac matching the target architecture. `PYTHON` chooses the bundle Python; `LIBUSB_DYLIB` selects a replacement library with a `COPYING` file in the parent of its `lib` directory. See `docs/third-party.txt` for dependency licensing.

For Python-only use, `python -m pip install ./mac` installs `pocket-dimension` into the active environment. Reinstall after source changes; the installed package is a copy.

## Verification

```sh
./scripts/test-security.sh
PYTHONPATH=mac .venv/bin/python -m unittest discover -s tests -v
./scripts/test-gui.sh
./gradlew :app:assembleDebug :app:assembleDebugAndroidTest :app:lintDebug
```

`scripts/test-gui.sh` compiles the actual Mac GUI model and checks navigation, multi-selection, serial transfer batches, error handling, and destination safety against a temporary fake CLI. It does not connect to a phone.

Framework-only Android storage checks run on an emulator with all-files permission granted for testing:

```sh
adb -s emulator-5554 install -r app/build/outputs/apk/debug/app-debug.apk
adb -s emulator-5554 install -r app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk
adb -s emulator-5554 shell appops set dev.pocketpull MANAGE_EXTERNAL_STORAGE allow
adb -s emulator-5554 shell am instrument -w dev.pocketpull.test/dev.pocketpull.StorageChecks
```

These commands explicitly target the emulator. The test creates/removes only a uniquely named test directory under its Download folder. The Android app and test APK must use the same signing key.

Validation on 2026-10-23: 47 automated tests and three real Android storage contract checks passed. Gradle app/test builds and lint pass with zero errors and two advisory warnings (all-files-access policy and a newer Gradle version). The native Mac GUI launches, discovers the connected phone, and reports the required APK update. Physical USB acceptance of the new folder/upload/GUI features remains pending.

Current validation and hardware-only follow-ups are recorded in standard todo.txt syntax in `todo.txt`. See [wire protocol](protocol.md) and [hardware checklist](hardware-validation.md).

## Troubleshooting

- **No phone:** unlock it, select File transfer in Android USB settings, try a data cable, or use `devices --all` and explicit IDs.
- **Busy/access denied:** close Android File Transfer/OpenMTP and other USB software; unplug/reconnect. Pocket Dimension does not detach drivers or claim the ADB interface.
- **AOA timeout:** use a direct Mac port and known data cable. Accessory support varies by phone.
- **Read timeout:** approve the accessory dialog, open Pocket Dimension, tap Connect USB, and try `status`.
- **Browse/upload says update APK:** install the 0.3+ Android APK. Older apps still support single-file pulls.
- **Cannot browse a location:** share the desired folder, or explicitly enable device-storage access. Android-protected locations remain restricted.
- **Destination exists:** choose Keep Both or Skip in the GUI, or `--conflict keep-both`/`--conflict skip` in the CLI. There is no overwrite flag.
- **Transfer interrupted:** use Retry unfinished after a confirmed cancellation. For device disconnects or protocol failures, reconnect to the same phone/scope first. A failed recursive upload may have left earlier verified files in place.

## Rebrand compatibility

Pocket Dimension was previously called PocketPull. The primary command is now `pocket-dimension`; `pocketpull` remains a compatibility alias in both Python installs and standalone builds. The Python distribution/module name, Android package ID (`dev.pocketpull`), Mac bundle ID, signing keys, and legacy AOA manufacturer/model are retained to preserve updates and USB compatibility. The app also recognizes the new Pocket Dimension accessory identity. HELLO now reports the new display name; the wire protocol is version 4 in v0.5, with older client protocol compatibility. Quit the older Mac app before opening the renamed app.

Validation for v0.4 (2026-10-24): 61 Python/codec/client checks, GUI cancellation/skip/retry checks, and four Android emulator storage/cancellation checks pass. Gradle build/lint pass. Physical USB cancellation and reconnect acceptance with the new APK remain on the hardware checklist.
