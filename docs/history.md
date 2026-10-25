# Development history

Historical milestone notes. For current setup and security behavior, use the [README](../README.md) and [security design](secure-usb-design.md).

## v0.5: resume, queue history, and Mac packaging

Install the matching v0.5 APK and rebuilt Mac app. Resume is enabled by default in the GUI; CLI examples:

```sh
pocket-dimension pull ~/Downloads/large.bin --remote large.bin --resume
pocket-dimension push ./large.bin --remote large.bin --resume
```

Retry the same source and destination after interruption. Retained prefixes are hashed on both ends before any suffix is accepted, and complete files are checksum-verified before final save. Downloads retain partial bytes privately on the Mac; uploads retain them in the phone app's private cache. Cancellation leaves final destinations absent. Android can evict its cache, in which case the transfer starts again. Full source hashing and local cache copies add verification time and require temporary free space.

The Mac table sorts by Name, Kind, Size, or Modified; folders stay first. Drop local files/folders onto the table to queue uploads into the current folder. The queue shows total bytes, remaining items, and ETA when sizes/rate are known. History survives relaunch, including interrupted jobs waiting for explicit Retry unfinished. The current phone and sharing scope must match. File paths in private history are stored locally in ~/Library/Application Support/PocketDimension/history.json.

Build a local installer with `./scripts/build-release.sh`. Artifacts are under dist, including PocketDimension-0.6.0-arm64.dmg and its SHA-256 manifest. The installer contains the Mac app and Android APK. Local builds are ad-hoc/debug signed; see [release instructions](releases.md) for compatible updates and Developer ID/notarization setup. Production signing needs your signing certificates.


Validation: 73 Python checks, GUI navigation/queue/history/sort/drop checks, Gradle build/lint, and physical USB resume/recursive/conflict checks pass. Physical permission revocation/restoration and the real system folder-picker provider also pass using generated files. See [hardware evidence and remaining checks](hardware-validation.md).


## Identifying multiple phones (v0.5.1)

The Mac phone picker shows the model and an identifier, followed by its USB bus/address. Before accessory mode, the identifier is the phone's USB serial when readable. In accessory mode, the updated Android app reports its manufacturer/model and a stable Pocket Dimension phone ID. The picker shows the last 12 characters of that ID, so identical models still have distinct labels. Clearing app data or reinstalling changes this app ID; it is separate from the hardware serial. USB locations remain available as a fallback for older, busy, or unapproved apps.

`pocket-dimension devices --details` prints the same information; add `--json` for full identifiers. Detailed discovery sends only HELLO to available accessories, never switches USB modes, and skips busy interfaces. Model reporting requires the v0.5.1 APK. It uses no additional Android permissions.


The v0.5.1 GUI bundles a pre-extracted Python runtime, avoiding the roughly 2.4-second unpacking cost on each folder navigation. Local warm-start measurements fell to 0.06–0.07 seconds for helper startup/discovery; first launch can still take longer. Phone/provider directory enumeration and USB time are additional. Listings are read fresh rather than cached. The standalone single-file CLI is still available for portability.


## Blue portal theme (v0.5.2)

The Mac and Android apps use matching blue/cyan portal artwork and updated icons. The desktop header, folder accents, drop highlight, and progress controls follow the blue palette. Android has coordinated light and navy dark palettes, outlined cards, and primary/secondary blue controls. Both continue to follow the system appearance.


## Measured USB speeds

A verified 3 GB test on the connected S23 Ultra measured **12.2 MB/s upload** (4m 7s) and **12.8 MB/s download** (3m 54s), including the app's verification/staging work. The negotiated link was 480 Mbps. Both checksums and an independent downloaded-file hash passed; test files were cleaned up. See [benchmark methodology and raw-results location](usb-performance.md) for conditions and the next optimization experiment.


## Faster transfers with batching (v0.6)

Batched acknowledgments are negotiated automatically when both apps support them; older builds keep per-frame behavior. Batches hold at most 16 existing USB frames (about 256 KB), with cancellation at confirmed boundaries and unchanged full checksum/overwrite protections. The same-link 512 MiB comparison measured **18.0 → 32.6 MB/s upload** and **22.7 → 71.1 MB/s download** on the current 5 Gbps connection. See [conditions and results](usb-performance.md). Use CLI `--no-batching` for a comparison. Routine benchmark samples now default to 512 MiB.
