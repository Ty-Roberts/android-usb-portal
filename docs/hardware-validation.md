# Physical phone validation

Record phone model, Android version, Mac/macOS version, cable, initial VID:PID, and whether other USB transfer apps were running. Use a local non-sensitive test file. Keep USB debugging disabled throughout to confirm the intended workflow.

1. Install the APK and open Pocket Dimension. Confirm no network permission is requested and broad storage access is optional, activated only through Share device storage.
2. Connect one unlocked phone with a data cable. Run `pocket-dimension devices`; record its IDs.
3. Run `pocket-dimension probe --vid <vendor> --pid <product> --switch`. Record protocol/USB errors. Confirm the phone reappears in AOA mode, launches Pocket Dimension, and interface 0 can be claimed. Approve the accessory dialog.
4. Run `pocket-dimension status` before selecting a file; expect `selected: false`. A pull should fail without creating a destination.
5. Choose a known local file through the system picker. Confirm `status` reports the selected name/size. Pull to an absent destination. Compare the Mac's SHA-256 and bytes with an independently known original, not only the digest reported by the app. Open the resulting file.
6. Repeat with a zero-byte file, a binary file larger than 16 KB, a large file, and a filename containing Unicode. Check a document provider with unknown size if available.
7. Retry with an existing destination; expect refusal and unchanged content.
8. Disconnect midway through a large transfer; expect no final destination and no temporary file after normal error cleanup. Reconnect and retry successfully.
9. Clear or replace selection during a large transfer. Expect the current transfer to fail, and confirm the cleared file cannot be pulled afterward.
10. Cancel the file picker. Confirm the previous approved selection is unchanged. Clear selection while metadata is being loaded; confirm it cannot reappear afterward.
11. Rotate the phone, change text size, and check narrow/landscape layouts. Background and foreground the app; confirm messages and selection remain coherent. Keep the app visible for supported transfer use.
12. Restart/force-stop the app; confirm selection approval is gone. Test denial of accessory permission and recover with Connect USB. Test detach/reconnect and repeated CLI invocations.
13. Run `pocket-dimension connect` after unplug/replug without specifying IDs; confirm automatic discovery and switching. Use `devices --all` to test manual fallback for unrecognized IDs.
14. Toggle Android system dark mode; check text, cards, buttons, and system bars. Theme changes recreate the activity, so select a file again afterward.
15. If available, connect two matching phones; confirm ambiguous selection fails until a bus/address is supplied. Ensure another phone already in AOA mode is never mistaken for the switched phone.

On 2026-10-23 the user confirmed a successful physical USB download on a Samsung phone after the Mac empty-packet fix. Remaining checklist cases and compatibility with other phones still need validation. Automatic connection and the system dark theme were added afterward; validate them on hardware too.

## v0.3 folder, upload, and GUI acceptance

1. Install the new APK using the same signing route as the previous app. Connect using the v0.3 CLI or GUI.
2. Choose a folder containing nested folders, an empty directory, empty files, Unicode names, and binary content. Browse it from CLI and GUI. Pull it to an absent destination and compare independent file checksums.
3. Upload a file and a nested directory. Verify stored bytes independently on the phone. Retry an existing filename; ensure the original is unchanged.
4. Interrupt an upload with a cable disconnect and with Stop sharing. Ensure the current incomplete file is removed after the phone handles the interruption; earlier completed directory-upload files may remain.
5. Enable device-storage access through Android settings and return to the app. Confirm the shared-storage root appears and protected Android locations fail cleanly. Revoke the setting and verify subsequent access fails.
6. Deny all-files access and test the folder picker instead. Test at least the system local-storage provider; third-party providers may be read-only or unable to support uploads.
7. Verify terminal progress for known and unknown file sizes, and GUI progress while downloading/uploading large files. Test no-overwrite error reporting in both interfaces.
8. Confirm GUI selection of multiple connected phones, folder navigation/up navigation, explicit destinations, and reconnecting after the Android sharing scope changes.

Automated/emulator evidence: 47 client/codec tests and three Android storage checks pass. GUI launch/device discovery and the old-APK upgrade message were verified. These do not replace the USB acceptance cases above.

## v0.4 cancellation and recovery acceptance

1. Install the v0.4 APK and Mac app. Start a large download, cancel it, and confirm no final or temporary local file remains. Start another transfer without unplugging.
2. Start a large upload, cancel it, and verify the phone removes the incomplete file. Retry the same filename and verify its checksum after success.
3. Queue multiple items. Skip a running item and confirm the next begins only after cleanup. Cancel the queue and confirm remaining items stay Waiting.
4. Introduce a failure, then use Retry unfinished. Confirm previously completed files are not downloaded/uploaded again. Test Skip failed and continue.
5. Exercise Stop, Skip, and Keep Both against existing files, directories, dangling local symlinks, and duplicate names. Independently verify original content remains unchanged. Check Keep Both extension-preserving names and Reveal in Finder.
6. Disconnect/reconnect the same phone, Connect again, and re-share the same folder before retrying. Verify queued phone/scope identity checks. Choose a different scope/phone and verify the job is refused before moving data.
7. Test cancellation during a provider delay and during final verification. The UI may remain Cancelling until a USB read returns; a file already verified is retained.
8. Optionally exercise an older APK: cancellation drains an active download, so it is slower but leaves framing synchronized. Upload cancellation must delete its partial output.

Automated evidence for this milestone: 61 client/codec tests, GUI cancellation/skip/retry process checks, and four Android storage/cancellation emulator checks passed. Physical USB cancellation and reconnect with the new APK still require these acceptance checks.

## v0.5 physical evidence (2026-10-24)

Samsung SM-S918U1, connected over AOA USB, updated using the installed APK's matching Gradle debug certificate. No uninstall or data reset was performed. The separate direct-SDK debug key is incompatible with this particular installation.

The opt-in StorageChecks USB fixture exposes only a generated app-owned folder and cleans it when the Mac sends its finish marker. `tests/phone_acceptance.py` passed seven checks against real Android code: protocol identity, recursive download with empty file, cancel/download/session reuse/resume, cancel/upload/private prefix/resume/readback, Skip/Keep Both/no-overwrite, recursive upload roundtrip, and byte planning/modification dates. Binary fixtures were 12,582,912 bytes. Result: `build/phone-acceptance.json`.

`tests/phone_permissions.py` verified real all-files revocation, storage refusal, and cleared sharing selection. Android terminates the app on revocation; restoration therefore runs in a host-side finally block. The phone's original UID allow and package default modes were confirmed afterward. A separate actual system folder-picker run, pointed at a generated Download/PocketDimension-permission-check folder, passed provider enumeration, mkdir, upload/readback, containment, and clear selection. The test folder was deleted and the picker activity finished; no persistent URI grant was requested.

These checks do not establish compatibility with third-party cloud providers or other phone models. Physical unplug/replug automatic AOA switching still needs the hands-on check.

To reproduce, explicitly select the phone serial for all adb commands. Build and install app-debug.apk plus app-debug-androidTest.apk using the same certificate as the installed app. Start `adb -s SERIAL shell am instrument -w -e usb true dev.pocketpull.test/dev.pocketpull.StorageChecks`, wait for USB_READY, then run `PYTHONPATH=mac .venv/bin/python tests/phone_acceptance.py`. That script refuses any selection whose name is not a generated fixture. For real GUI queue model checks, start another fixture and run `./scripts/test-phone-gui.sh`; finish the fixture afterward. For all-files tests use `tests/phone_permissions.py SERIAL`. For the picker use `am instrument -w -e permissions true ...` and approve only the exact generated folder shown by PICKER_READY. Do not grant the storage root or any personal folder for these tests.

`./scripts/test-phone-gui.sh` also passed against the physical generated scope: cooperative queue Cancel followed by prefix resume/retry, Skip followed by the next job, conflict refusal preserving original local bytes, retry of failed/waiting jobs, cumulative byte totals, saved/reloaded queue history, equal SHA-256 for five download outputs, and device/scope identity refusal before output creation. This exercises the actual Swift Model and standalone CLI against Android, without relying on mocked USB. Result: `build/phone-gui-acceptance.txt`. Native v0.5 controls and live folder listing/modification timestamps were inspected; later native window automation failed with cgWindowNotFound, so final installer UI interaction is not claimed as verified.


## v0.6 batching acceptance

89 Python client/codec tests and GUI checks passed, as did Gradle build/lint. Six Android checks passed on the physical phone, including legacy per-chunk cancellation, a final partial download batch followed by cancellation, upload batch offsets, and rejection of an oversized batch. The real USB generated-file suite passed recursive transfers, empty files, cancellation/session reuse, resumed prefix verification, conflicts, and readback. The controlled 512 MiB A/B test and the completed 3 GB batched run passed checksums and independent download rehashes. See docs/usb-performance.md; all generated fixtures and the test-only instrumentation were cleaned up. Public Developer ID/notarization and the separate hands-on reconnect checklist remain outstanding.
