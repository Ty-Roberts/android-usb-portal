# Pocket Dimension wire protocol

The legacy AOA manufacturer/model `PocketPull`, version `1`, remain unchanged for USB accessory discovery. Current application protocol is **5**, requiring authenticated encryption before file operations. Versions 1–4 below describe historical transfer behavior; production clients do not fall back to those plaintext protocols.

## Version 5: secure session envelope

Before authorization, only empty HELLO (type 1) and empty SECURE (type 13) upgrade requests are accepted. Public HELLO returns version, capabilities, model and app device ID, never scope or file metadata. After upgrade all outer frames are type 13, containing 1–16,379 bytes of TLS ciphertext. TLS records may span frames. Mutual TLS uses self-signed P-256 identities, followed by explicit certificate fingerprint authorization; successful TLS alone does not grant application access.

Inside TLS, controls are uint32 big-endian length plus UTF-8 JSON (2–8,192 bytes). Client sends `mode` = `inspect`, `connect`, `pair`, or `revoke`. Connect/pair includes `expected_phone_fingerprint`; pair also includes printable ASCII `host_name` (1–80 characters). Responses bind `phone_fingerprint` and `host_fingerprint` (full lowercase SHA-256 certificate hashes). Inspect returns `trusted` or `pairing_required` without application access. Unknown connect returns `pairing_required`. Revoke returns `revoked` and removes only the authenticated caller's grant.

Pair requires both physical screens to display and compare both full fingerprints, then explicit phone approval. Phone sends `approved` with a random 32-byte base64 `confirmation`; client echoes that field on the same channel. Only then does the library persist a fixed seven-day grant and return `authorized` with integer epoch-millisecond `expires_at`. Denial returns `denied`. Connect requires an unexpired stored grant. Unknown phones require explicit pin approval on the desktop; no automatic trust-on-first-use is performed.

Only after `authorized` do the application frames below begin **inside TLS**, starting with encrypted HELLO version 5. TLS close_notify finishes a logical session and returns to public discovery; physical detach, malformed traffic, expiry, revocation, or lock aborts access. No session resumption bypasses the application authorization gate. See [library API](../usb-android/README.md) and [security design](secure-usb-design.md).


Frames: one-byte type, four-byte unsigned big-endian payload length, then payload. Maximum payload is 16,379 bytes, making each whole frame at most 16,384 bytes. Readers accept arbitrary fragmentation/coalescing. Android buffers full-size accessory reads. Successful zero-byte USB bulk transfers are packet boundaries, not EOF; the host skips them without resetting its inactivity deadline.

| Type | Value | Request / response |
| --- | --- | --- |
| HELLO | 1 | Empty request; JSON `{version:3,app:"Pocket Dimension",features:["browse","folders","push"]}` response |
| LIST | 2 | Empty request; META with current sharing scope or `{selected:false}` |
| GET | 3 | Empty for selected file, or JSON `{path:"relative/file"}`; META, DATA*, END |
| META | 4 | JSON metadata, directory entry, or upload acknowledgment |
| DATA | 5 | Raw file bytes, either direction |
| END | 6 | JSON checksum/byte count or listing count |
| ERROR | 7 | UTF-8 error description |
| BROWSE | 8 | JSON `{path:"relative/folder"}`; one META per child, then END `{entries:N}` |
| PUSH | 9 | JSON `{path:"relative/new-file",size:N}`; META `{ready:true}` then upload sequence |
| MKDIR | 10 | JSON `{path:"relative/folder"}`; META for created/existing directory |
| STAT | 11 | JSON `{path:"relative/path"}`; META for that node |

All actual JSON has quoted keys. Empty path means the selected root. Paths must be relative, with no empty components, `.`/`..`, backslashes, control characters, or symbolic links. Component length is bounded at 512 characters, path length at 8,192, depth at 128, and listing/tree traversal at 100,000 entries. Desktop filenames from the peer are validated before creating local paths. Duplicate provider names are rejected.

Metadata: `{name,type:"file"|"directory",size:number|null,writable:boolean}`. LIST additionally includes `selected:true` and `scope:"file"|"folder"|"shared-storage"`. File-only selection cannot be used to browse siblings or upload.

Downloads: GET returns META, zero or more DATA frames, then END `{bytes:N,sha256:"lowercase hex"}`. The host independently hashes every byte before creating its final local file. Empty files have zero DATA frames. Directory downloads are a client-side traversal of BROWSE and GET; every file has its own digest. Each listing has an explicit terminal count.

Uploads: after PUSH readiness, the host sends one DATA frame at a time and waits for META `{bytes:cumulative}` after each. This provides backpressure and surfaces phone-side errors before more chunks are sent. The host then sends END `{bytes,sha256}`. Android compares size/digest, closes the destination, reads it back, and returns END only after the stored bytes independently match. Uploads never overwrite existing files. An incomplete file may be visible while writing; it is deleted on a normal failure or cancellation that reaches the phone. Disconnect the USB cable after an interrupted operation to clear pending frames and ensure phone-side cleanup.

MKDIR reuses existing directories but rejects an existing file. Recursive uploads append into existing directories; an error may leave earlier verified files and created directories. There is no delete/replace command exposed to the host.

Changing/clearing the Android sharing selection revokes future operations and interrupts active transfers on their next read/check. All-files scope rechecks the Android permission. The scope is primary shared storage, not a root shell, app-private data, or unrestricted Android/data access.

One sequential operation per USB connection, no pipelining or resume. Malformed lengths, USB errors, and incomplete streams terminate the session. After an interrupted stream, unplug/reconnect before starting a new session.

## Version 3: cancellation and retry identity

The v0.4 Android APK advertises protocol version 3 and a `cancel` feature. The client accepts versions 1–3. HELLO includes an opaque `device_id`, generated once in private app preferences. LIST includes `scope_id`, a hash identifying the selected document URI or canonical shared-storage root. GUI queue items bind to both IDs. A retry can use a changed USB address only after Connect identifies the same phone; the CLI rejects a mismatched device or sharing scope before transferring data. Re-selecting the same document-tree root preserves its scope identity. Reinstalling/clearing app data creates a new phone ID.

CANCEL has frame type 12 and an empty payload. On cancellation the phone closes the current operation, deletes an uncommitted upload, and replies with END `{cancelled:true}`. That acknowledgment is consumed before the next operation; a new HELLO can reuse the session without unplugging.

A v3 GET supplies `{path:"...",acknowledge:true}`. The phone waits after each DATA frame for META `{bytes:cumulative}`. At this boundary the client sends either the acknowledgment or CANCEL. This prevents a large download from continuing to stream after the cancellation request. Empty files finish with END without a data acknowledgment. Non-acknowledged GET remains available for older clients.

Uploads already have an acknowledgment per chunk; the host sends CANCEL at the next safe boundary instead of another chunk or END. Cancellation during stored-file verification may arrive too late to stop that already-completed file; the GUI keeps it and stops remaining queue items.

For v2 download cancellation, the client drains the remaining frames through END and discards local temporary output. This is compatible but can be slow for large files. A v2 upload is cancelled by sending ERROR and consuming the receiver's ERROR response after partial-file cleanup. Transport loss or a malformed reply is not treated as a clean cancellation acknowledgment.

SIGINT/SIGTERM on transfer commands request cooperative cancellation rather than throwing midway through a USB frame. A synchronized cancellation exits with status 130. GUI Skip advances only after that clean cancellation status; other errors pause the queue.

## Conflict policy

`--conflict error` is the CLI default. `skip` returns a result with `status:"skipped"` without replacing existing output. `keep-both` chooses a new name such as `photo (2).jpg`; all final creates remain exclusive. Download results include the actual `destination` so GUI Reveal targets the kept copy.

For an existing local directory, Skip skips the whole directory and Keep Both downloads into a new sibling directory. For uploads, Skip merges directories while skipping existing files. Keep Both creates a new sibling for a conflicting directory. Previously completed files can remain after a recursive upload error; retry is not byte-level resume. A concurrent create can still cause a safe failure, never an overwrite.

## Version 4: verified resume and timestamps (v0.5)

The client accepts versions 1–4. HELLO v4 includes `resume`; file/directory metadata may include `modified` in epoch milliseconds (null when unavailable).

For resumable GET, send `{path, acknowledge:true, offset, prefix_sha256}`. Android reads and hashes the prefix before sending META. It refuses an offset outside the source or a mismatched prefix with `RESUME_MISMATCH`; META echoes the accepted offset. DATA contains only the suffix. Chunk acknowledgments and END byte count cover the entire file, including the prefix. END includes the full SHA-256. The Mac stores partial data in an identity-bound private cache and creates the destination exclusively only after complete verification. Source device, scope, path, destination, size, and modification timestamp contribute to the cache key. The prefix is verified even when metadata is missing or unchanged.

For resumable PUSH, send `{path,size,resume:true,sha256}` using the full local source hash. Android replies META `{ready:true,offset,prefix_sha256}` for its retained private partial. The Mac verifies that prefix locally before sending more bytes. The phone validates full size/hash and copies to a newly created destination, reads it back, and verifies the stored checksum before committing. Existing destinations remain protected. A source change uses a new content identity; a corrupt prefix is discarded with CANCEL `{discard:true}` and requires retry from the beginning.

Normal cancellation retains resumable private cache data; CANCEL with `discard:true` discards an upload prefix. END `{cancelled:true}` confirms the boundary. The client reports cancellation success only after confirmation. Non-resume transfers retain v3 cleanup behavior. Resume is opt-in in CLI (`--resume`) and enabled by default in the GUI. Older APKs transfer from the beginning. Cache data can be evicted; entries older than seven days are pruned when caches are used. Completed transfers remove their cached prefix.

`plan` and `local-plan` are client-side traversals that count files and known bytes before a GUI batch. They enforce traversal limits. Unknown sizes remain indeterminate. Estimates can change as remote files change; final checksums and exclusive saves remain authoritative.

HELLO v4 in v0.5.1 also includes `manufacturer` and `model` from Android Build metadata. These optional display fields do not change device/scope authorization. The install-scoped `device_id` remains the retry identity. AOA USB serial strings identify the host accessory, so the client must not display them as a phone serial.

## Bounded batch acknowledgments (v0.6)

HELLO retains protocol version 4 and optionally advertises `batch-ack`. A client requests `batch_frames:16` on GET/PUSH only when that feature is present; the server echoes the accepted count in the initial META response. The hard limit is 16 DATA frames, each at most 16,379 bytes, for a maximum 262,064-byte batch. Older clients omit the option; older APKs omit the feature; both keep per-frame behavior. `--no-batching` disables batching for CLI comparisons.

For GET, the phone sends up to the negotiated number of DATA frames, then a META boundary `{batch:true,bytes:cumulative_total}`. The client consumes that bounded batch, validates the count, stores/hashes its bytes, and responds with META `{bytes:cumulative_total}` or CANCEL. A final partial batch also has a boundary before END. Empty files have no DATA batch. END still carries the complete size and SHA-256, including any resumed prefix.

For PUSH, the client sends up to the negotiated number of DATA frames, followed by the same META boundary. The phone collects the bounded batch before writing to storage or replying. It validates the cumulative count and declared size, rechecks the sharing approval, writes/hashes the batch, and acknowledges its cumulative bytes. This keeps queued DATA out of the command stream when a storage write fails. END is sent only after all batches are acknowledged and retains full size/hash checks and stored-file verification.

Cancellation waits for the current bounded batch boundary. The receiving side confirms END `{cancelled:true}` before the client reports cancellation success. Resumable transfers retain the acknowledged prefix; non-resume transfers remove the incomplete output. Memory and bytes in flight are bounded; an OS/provider I/O stall can still delay a response. No sharing permissions or overwrite policy changed.
