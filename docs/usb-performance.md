# USB performance baseline

Measured 2026-10-24 with Pocket Dimension v0.5.2 and the connected Samsung SM-S918U1 (Galaxy S23 Ultra), using the existing cable and port. macOS IORegistry reported UsbLinkSpeed=480000000: a 480 Mbps USB link during AOA mode.

Each direction transferred exactly 3,000,000,000 bytes (3.00 GB / 2.79 GiB) of freshly generated random data. Resume was enabled with no retained prefix, matching the GUI default. The generated fixture used the phone's already-granted shared-storage access mode, restricted to its own app-owned test directory.

| Direction | Total time | Effective throughput | Streaming throughput |
| --- | ---: | ---: | ---: |
| Mac → phone | 246.7 s | 12.16 MB/s | 12.88 MB/s |
| Phone → Mac | 234.3 s | 12.80 MB/s | 12.88 MB/s |

MB/s uses decimal megabytes. Effective timing includes the transfer call, source hashing, resume staging/copy, and app verification. Streaming timing spans initial through final payload progress. Generating the source and independently hashing the downloaded file afterward are excluded. The benchmark used a 60-second USB inactivity timeout; the measured final upload staging and verification completed within the GUI's normal 30-second timeout. This is one large-file run in each direction; caches were not flushed, and different providers, cables, storage, or thermal conditions may change results.

Both returned SHA-256 values matched the generated source, and an independent hash of the downloaded file matched too. All generated local and phone files were removed, and the test-only instrumentation package was uninstalled. Raw measurements are in build/usb-benchmark-3gb.json.

## Improving throughput

The current protocol waits for an acknowledgment after each roughly 16 KB payload. A bounded window with batched acknowledgments is the next experiment; it must retain cancellation boundaries, prefix verification, and final checksums. No speedup is established until measured.

The S23 Ultra is specified for USB 3.2 Gen 1. That standard signals at 5 Gbps, so a cable explicitly rated for at least 5 Gbps USB data is sufficient for the phone's stated capability. A faster-rated cable cannot establish the resulting AOA link speed by itself; repeat the negotiated-link check and benchmark after swapping cables. The present 480 Mbps observation does not prove whether the cable, port, or accessory-mode configuration imposed that limit.

Sources: [Samsung S23 Ultra specifications](https://www.samsung.com/ph/smartphones/galaxy-s/galaxy-s23-ultra-phantom-black-1tb-sm-s918bzkwphl/), [USB-IF speed definitions](https://www.usb.org/sites/default/files/usb_3_2_language_product_and_packaging_guidelines_final.pdf).

## Reproducing

Build/install the certificate-matched Android instrumentation APK. Start `adb -s SERIAL shell am instrument -w -e usb true -e benchmark true dev.pocketpull.test/dev.pocketpull.StorageChecks` and wait for USB_READY. Run `PYTHONPATH=mac .venv/bin/python tests/phone_benchmark.py`. The benchmark refuses any scope whose name is not the generated fixture, sends the finish marker afterward, removes local files, and writes its report. The harness cleans the phone fixture. Confirm cleanup and uninstall dev.pocketpull.test after the run. Avoid concurrent GUI transfers while benchmarking.


## v0.6 controlled batching comparison

Measured later on 2026-10-24 after the connection began negotiating **5 Gbps**. These results must not be compared to the earlier 480 Mbps run as a batching-only improvement.

The same v0.6 APK, connection, generated random source, and app-owned shared-storage fixture were used for each mode. Each sample was **512 MiB (536,870,912 bytes)**, per the user's preference for shorter speed tests. Resume was enabled and prefixes were empty. Rates below include the transfer call's hashing, staging, and verification, with independent post-transfer hashing excluded.

| Direction | Per-frame ACK | Batched ACK (16 frames) | Improvement | Time before → after |
| --- | ---: | ---: | ---: | ---: |
| Mac → phone | 17.99 MB/s | 32.62 MB/s | 1.81× | 29.85 s → 16.46 s |
| Phone → Mac | 22.72 MB/s | 71.14 MB/s | 3.13× | 23.63 s → 7.55 s |

The link was confirmed at 5 Gbps after the comparison too. Every transfer passed its SHA-256 checks, including independent downloaded-file rehashes. Generated local and phone files were removed and the test harness uninstalled. This is one sample per mode/direction, with unflushed filesystem caches; the rates are observations on this setup, not guarantees. Raw results: build/usb-benchmark-512mib-comparison.json.

An earlier run on that 5 Gbps link also completed 3 GB each way with batching: 34.39 MB/s effective upload (87.23 s) and 69.15 MB/s download (43.39 s), with full checksums and cleanup. Raw results: build/usb-benchmark-3gb-batched.json. Routine benchmark defaults are now 512 MiB, with --bytes available for explicit large-file acceptance.

For future A/B tests, use `PYTHONPATH=mac .venv/bin/python tests/phone_benchmark.py --compare-batching --report build/usb-comparison.json` after starting the generated benchmark fixture. This uses one 512 MiB source and compares both directions with batching disabled/enabled on the same connection.
